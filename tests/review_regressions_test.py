import asyncio
import copy
import json
import unittest
import urllib.parse
from decimal import Decimal, Inexact, Rounded, ROUND_DOWN, ROUND_UP, localcontext
from unittest.mock import AsyncMock, Mock, patch

import flask

from schwab import auth
from schwab.contrib.orders import (
    UnrepeatableOrderError, code_for_builder, construct_repeat_order)
from schwab.orders.common import OptionInstruction, OrderStrategyType
from schwab.orders.equities import equity_buy_market
from schwab.orders.generic import OrderBuilder
from schwab.orders.options import OptionSymbol
from schwab.streaming import StreamClient, StreamClosedError, UnexpectedResponseCode
from .utils import account_preferences, MockResponse


class StrikePrecisionTest(unittest.TestCase):
    def test_symbol_is_independent_of_decimal_context(self):
        expected = 'SPXW  260918C05432125'
        existing = OptionSymbol('SPXW', '260918', 'C', '5432.125')
        for precision in (1, 6, 28):
            for rounding in (ROUND_DOWN, ROUND_UP):
                with self.subTest(precision=precision, rounding=rounding):
                    with localcontext() as context:
                        context.prec = precision
                        context.rounding = rounding
                        context.traps[Inexact] = True
                        context.traps[Rounded] = True
                        self.assertEqual(existing.build(), expected)
                        self.assertEqual(OptionSymbol(
                            'SPXW', '260918', 'C', '5432.125').build(), expected)
                        self.assertEqual(
                            OptionSymbol.parse_symbol(expected).build(), expected)

    def test_strike_limits_are_exact_before_scaling(self):
        for value in ('1.0000000000000000000000000001', '0.0001',
                      '1e-1000000', '100000', '1e1000000'):
            with self.subTest(value=value), localcontext() as context:
                context.prec = 1
                with self.assertRaises(ValueError):
                    OptionSymbol('SPXW', '260918', 'C', value)
        for value, suffix in [('99999.999', '99999999'),
                              ('1e-3', '00000001'),
                              ('100.00000000', '00100000')]:
            with self.subTest(value=value), localcontext() as context:
                context.prec = 1
                self.assertTrue(OptionSymbol(
                    'SPXW', '260918', 'C', value).build().endswith(suffix))


class PriceDecimalContextTest(unittest.TestCase):
    def test_numeric_prices_ignore_callers_precision_and_rounding_traps(self):
        for precision in (1, 2, 6, 28):
            for rounding in (ROUND_DOWN, ROUND_UP):
                with self.subTest(precision=precision, rounding=rounding):
                    with localcontext() as context:
                        context.prec = precision
                        context.rounding = rounding
                        context.traps[Inexact] = True
                        context.traps[Rounded] = True
                        builder = (OrderBuilder()
                            .set_price(Decimal('12.349'))
                            .set_stop_price(Decimal('0.1234'))
                            .add_option_leg(
                                OptionInstruction.BUY_TO_OPEN, 'SYNTHETIC', 1))
                        built = builder.build()
                        self.assertEqual('12.34', built['price'])
                        self.assertEqual('0.12', built['stopPrice'])
                        self.assertFalse(context.flags[Inexact])
                        self.assertFalse(context.flags[Rounded])

    def test_negative_zero_and_large_exact_prices(self):
        with localcontext() as context:
            context.prec = 1
            self.assertEqual('0.00',
                             OrderBuilder().set_price(Decimal('-0')).build()['price'])
            price = '123456789012345678901234567890.12'
            self.assertEqual(price, OrderBuilder().set_price(
                Decimal(price)).build()['price'])
            self.assertEqual('1' + '0' * 100 + '.00',
                             OrderBuilder().set_price(
                                 Decimal('1e100')).build()['price'])
            with self.assertRaisesRegex(ValueError, 'too large'):
                OrderBuilder().set_price(Decimal('1e1000000'))

    def test_subcent_option_price_still_rejected(self):
        with localcontext() as context:
            context.prec = 1
            context.traps[Inexact] = True
            context.traps[Rounded] = True
            builder = (OrderBuilder().set_price(Decimal('0.005'))
                       .add_option_leg(OptionInstruction.BUY_TO_OPEN,
                                       'SYNTHETIC', 1))
            with self.assertRaisesRegex(ValueError, 'truncate to zero'):
                builder.build()
class CompositeOrderTest(unittest.TestCase):
    def test_unsupported_child_counts_are_rejected_in_both_paths(self):
        cases = [('TRIGGER', 0), ('TRIGGER', 2), ('OCO', 0),
                 ('OCO', 1), ('OCO', 3), ('BLAST_ALL', 2), ('SINGLE', 1)]
        for strategy, count in cases:
            with self.subTest(strategy=strategy, count=count):
                builder = OrderBuilder().set_order_strategy_type(
                    OrderStrategyType[strategy])
                for _ in range(count):
                    builder.add_child_order_strategy(
                        equity_buy_market('AAPL', 1))
                with self.assertRaises(UnrepeatableOrderError):
                    construct_repeat_order(builder.build())
                with self.assertRaises(UnrepeatableOrderError):
                    code_for_builder(builder)

    def test_nested_unsupported_children_are_rejected(self):
        malformed = OrderBuilder().set_order_strategy_type(OrderStrategyType.OCO)
        parent = (OrderBuilder().set_order_strategy_type(OrderStrategyType.TRIGGER)
                  .add_child_order_strategy(malformed))
        with self.assertRaises(UnrepeatableOrderError):
            construct_repeat_order(parent.build())
        with self.assertRaises(UnrepeatableOrderError):
            code_for_builder(parent)

    def test_invalid_child_representations_are_rejected(self):
        with self.assertRaises(UnrepeatableOrderError):
            construct_repeat_order({'orderStrategyType': 'TRIGGER',
                                    'childOrderStrategies': {'orderId': 1}})
        with self.assertRaises(UnrepeatableOrderError):
            construct_repeat_order({'orderStrategyType': 'TRIGGER',
                                    'childOrderStrategies': [None]})
        with self.assertRaises(UnrepeatableOrderError):
            code_for_builder(OrderBuilder()
                .set_order_strategy_type(OrderStrategyType.TRIGGER)
                .add_child_order_strategy({'orderStrategyType': 'SINGLE'}))


class StreamLifecycleTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.http_client = Mock()
        self.http_client.get_user_preferences.return_value = MockResponse(
            account_preferences(), 200)
        self.http_client.token_metadata.token = {'access_token': 'synthetic-token'}
        self.client = StreamClient(self.http_client, max_pending_handler_tasks=2)

    async def asyncTearDown(self):
        await self.client._close_connection()

    def socket(self, code=0):
        socket = AsyncMock()
        incoming = asyncio.Queue()

        async def send(raw):
            request = json.loads(raw)['requests'][0]
            incoming.put_nowait(json.dumps({'response': [{
                'service': request['service'], 'command': request['command'],
                'requestid': request['requestid'],
                'content': {'code': code, 'msg': 'synthetic response'}}]}))

        socket.send.side_effect = send
        socket.recv.side_effect = incoming.get
        return socket

    def buffer_messages(self, count):
        self.client._socket = self.socket()
        self.client._overflow_items.extend([
            {'data': [{'service': 'CHART_EQUITY', 'sequence': index,
                       'content': [{'5': 123.45}]}]}
            for index in range(count)])

    async def test_cancelled_delivery_preserves_fifo(self):
        self.buffer_messages(2)
        seen = []
        self.client.add_chart_equity_handler(lambda msg: seen.append(msg['sequence']))
        messages = list(self.client._overflow_items)
        self.client._overflow_items.clear()

        async def deliver_and_cancel():
            self.client._deliver_message(messages[0])
            self.client._overflow_items.extend(messages[1:])
            consumer.cancel()

        with patch.object(self.client, '_reader_loop', deliver_and_cancel):
            consumer = asyncio.create_task(self.client.handle_message())
            with self.assertRaises(asyncio.CancelledError):
                await consumer
        await self.client.handle_message()
        await self.client.handle_message()
        self.assertEqual(seen, [0, 1])

    async def test_logout_fails_waiting_and_queued_consumers(self):
        self.buffer_messages(0)
        consumers = [asyncio.create_task(self.client.handle_message())
                     for _ in range(2)]
        await asyncio.sleep(0)
        await self.client.logout()
        results = await asyncio.gather(*consumers, return_exceptions=True)
        for task, result in zip(consumers, results):
            self.assertIsInstance(result, StreamClosedError)
            self.assertFalse(task.cancelled())

    async def test_reconnect_fails_existing_consumer(self):
        self.buffer_messages(0)
        consumer = asyncio.create_task(self.client.handle_message())
        await asyncio.sleep(0)
        with patch('schwab.streaming.ws_client.connect',
                   new_callable=AsyncMock, return_value=self.socket()):
            await self.client.login()
        with self.assertRaises(StreamClosedError):
            await consumer
        self.assertFalse(consumer.cancelled())

    async def test_disconnect_fails_pending_subscription(self):
        self.buffer_messages(0)
        self.client._socket.send.side_effect = None
        subscription = asyncio.create_task(self.client.chart_equity_subs(['AAPL']))
        await asyncio.sleep(0)
        await self.client._close_connection()
        with self.assertRaises(StreamClosedError):
            await subscription
        self.assertFalse(subscription.cancelled())

    async def test_task_group_reports_disconnect(self):
        self.buffer_messages(0)
        with self.assertRaises(ExceptionGroup) as raised:
            async with asyncio.TaskGroup() as group:
                group.create_task(self.client.handle_message())
                await asyncio.sleep(0)
                await self.client.logout()
        self.assertTrue(all(isinstance(exc, StreamClosedError)
                            for exc in raised.exception.exceptions))

    async def test_cancelled_delivery_is_not_restored_after_disconnect(self):
        self.buffer_messages(1)
        message = self.client._overflow_items.popleft()

        async def disconnect_and_cancel():
            self.client._deliver_message(message)
            consumer.cancel()
            await self.client._close_connection()

        # Run cleanup immediately after the reader has delivered the message.
        with patch.object(self.client, '_reader_loop', disconnect_and_cancel):
            consumer = asyncio.create_task(self.client.handle_message())
            with self.assertRaises(asyncio.CancelledError):
                await consumer
        self.assertFalse(self.client._overflow_items)

    async def test_slow_handlers_apply_backpressure_before_task_creation(self):
        self.buffer_messages(3)
        gates = [asyncio.Event() for _ in range(3)]
        started = [asyncio.Event() for _ in range(3)]

        async def handler(message):
            index = message['sequence']
            started[index].set()
            await gates[index].wait()

        self.client.add_chart_equity_handler(handler)
        await self.client.handle_message()
        await self.client.handle_message()
        await asyncio.wait_for(started[1].wait(), 1)
        third = asyncio.create_task(self.client.handle_message())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertFalse(third.done())
        self.assertFalse(started[2].is_set())
        self.assertEqual(len(self.client._handler_tasks), 2)
        gates[0].set()
        await asyncio.wait_for(third, 1)
        await asyncio.wait_for(started[2].wait(), 1)
        self.assertEqual(len(self.client._handler_tasks), 2)

    async def test_shutdown_cancels_handlers_and_abandons_blocked_dispatch(self):
        self.client = StreamClient(self.http_client, max_pending_handler_tasks=1)
        self.buffer_messages(2)
        started = asyncio.Event()
        seen = []

        async def handler(message):
            seen.append(message['sequence'])
            started.set()
            await asyncio.Event().wait()

        self.client.add_chart_equity_handler(handler)
        await self.client.handle_message()
        await started.wait()
        second = asyncio.create_task(self.client.handle_message())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await asyncio.wait_for(self.client.logout(), 1)
        with self.assertRaises(StreamClosedError):
            await asyncio.wait_for(second, 1)
        self.assertEqual(seen, [0])
        self.assertFalse(self.client._handler_tasks)
        self.assertIsNone(self.client._socket)

    async def test_cancelled_capacity_wait_resumes_without_duplicate_handlers(self):
        self.client = StreamClient(self.http_client, max_pending_handler_tasks=1)
        self.buffer_messages(2)
        gate = asyncio.Event()
        started = asyncio.Event()
        seen = []

        async def slow(message):
            seen.append(('slow', message['sequence']))
            started.set()
            await gate.wait()

        self.client.add_chart_equity_handler(slow)
        self.client.add_chart_equity_handler(
            lambda msg: seen.append(('fast', msg['sequence'])))
        consumer = asyncio.create_task(self.client.handle_message())
        await asyncio.wait_for(started.wait(), 1)
        consumer.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await consumer
        gate.set()
        await asyncio.wait_for(self.client.handle_message(), 1)
        await asyncio.wait_for(self.client.handle_message(), 1)
        self.assertEqual(seen, [('slow', 0), ('fast', 0),
                                ('slow', 1), ('fast', 1)])

    async def test_handler_can_initiate_logout(self):
        self.buffer_messages(1)
        finished = asyncio.Event()

        async def handler(message):
            await self.client.logout()
            finished.set()

        self.client.add_chart_equity_handler(handler)
        await self.client.handle_message()
        await asyncio.wait_for(finished.wait(), 1)
        self.assertIsNone(self.client._socket)

    async def test_failed_handlers_release_capacity(self):
        self.client = StreamClient(self.http_client, max_pending_handler_tasks=1)
        self.buffer_messages(1)

        async def fail(message):
            raise RuntimeError('synthetic failure')

        self.client.add_chart_equity_handler(Mock(side_effect=RuntimeError('sync')))
        self.client.add_chart_equity_handler(fail)
        last = Mock()
        self.client.add_chart_equity_handler(last)
        with self.assertLogs('schwab.streaming', level='ERROR'):
            await asyncio.wait_for(self.client.handle_message(), 1)
        last.assert_called_once()
        self.assertFalse(self.client._handler_tasks)

    def test_invalid_handler_capacity(self):
        for limit in (0, -1, None, True, 1.5):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                StreamClient(self.http_client, max_pending_handler_tasks=limit)

    async def test_relogin_closes_previous_socket(self):
        old, new = self.socket(), self.socket()
        with patch('schwab.streaming.ws_client.connect',
                   AsyncMock(side_effect=[old, new])):
            await self.client.login()
            await self.client.login()
        old.close.assert_awaited_once()
        self.assertIs(self.client._socket, new)

    async def test_failed_reconnect_closes_previous_socket(self):
        old = self.socket()
        self.client._socket = old
        with patch('schwab.streaming.ws_client.connect',
                   AsyncMock(side_effect=OSError('connection failed'))):
            with self.assertRaises(OSError):
                await self.client.login()
        old.close.assert_awaited_once()
        self.assertIsNone(self.client._socket)

    async def test_failed_login_closes_new_socket(self):
        socket = self.socket(code=21)
        with patch('schwab.streaming.ws_client.connect', AsyncMock(return_value=socket)):
            with self.assertRaises(UnexpectedResponseCode):
                await self.client.login()
        socket.close.assert_awaited_once()
        self.assertIsNone(self.client._socket)

    async def test_missing_token_after_connect_closes_new_socket(self):
        socket = self.socket()
        self.http_client.token_metadata.token = {}
        with patch('schwab.streaming.ws_client.connect', AsyncMock(return_value=socket)):
            with self.assertRaises(KeyError):
                await self.client.login()
        socket.close.assert_awaited_once()
        self.assertIsNone(self.client._socket)

    async def test_login_timeout_closes_socket(self):
        self.client = StreamClient(self.http_client, response_timeout=0.01)
        socket = self.socket()
        socket.send.side_effect = None
        with patch('schwab.streaming.ws_client.connect', AsyncMock(return_value=socket)):
            with self.assertRaises(asyncio.TimeoutError):
                await self.client.login()
        socket.close.assert_awaited_once()
        self.assertIsNone(self.client._socket)

    async def test_cancelled_login_closes_socket(self):
        socket = self.socket()
        sent = asyncio.Event()
        socket.send.side_effect = lambda raw: sent.set()
        with patch('schwab.streaming.ws_client.connect', AsyncMock(return_value=socket)):
            task = asyncio.create_task(self.client.login())
            await asyncio.wait_for(sent.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        socket.close.assert_awaited_once()
        self.assertIsNone(self.client._socket)

    async def test_logout_error_and_timeout_close_socket(self):
        for timeout in (False, True):
            with self.subTest(timeout=timeout):
                # Only the timeout case needs a short deadline. Slower runners
                # must have time to deliver the mocked error response.
                self.client = StreamClient(
                    self.http_client, response_timeout=0.01 if timeout else 1.0)
                socket = self.socket(code=9)
                if timeout:
                    socket.send.side_effect = None
                self.client._socket = socket
                expected = asyncio.TimeoutError if timeout else UnexpectedResponseCode
                with self.assertRaises(expected):
                    await self.client.logout()
                socket.close.assert_awaited_once()
                self.assertIsNone(self.client._socket)
                self.assertIsNone(self.client._reader_task)

    async def test_cancelled_logout_closes_socket(self):
        socket = self.socket()
        sent = asyncio.Event()
        socket.send.side_effect = lambda raw: sent.set()
        self.client._socket = socket
        task = asyncio.create_task(self.client.logout())
        await asyncio.wait_for(sent.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        socket.close.assert_awaited_once()
        self.assertIsNone(self.client._socket)

    async def test_notification_handlers_receive_independent_messages(self):
        self.client._socket = self.socket()
        message = {'service': 'CHART_EQUITY', 'content': {'value': 123.45}}
        self.client._overflow_items.append({'notify': [message]})
        seen = []
        self.client.add_chart_equity_handler(
            lambda msg: msg['content'].update(value=0))
        self.client.add_chart_equity_handler(
            lambda msg: seen.append(msg['content']['value']))
        await self.client.handle_message()
        self.assertEqual(seen, [123.45])
        self.assertEqual(message['content']['value'], 123.45)

    async def test_handlers_receive_independent_nested_messages(self):
        for asynchronous in (False, True):
            with self.subTest(asynchronous=asynchronous):
                self.client = StreamClient(self.http_client)
                self.buffer_messages(1)
                original = copy.deepcopy(self.client._overflow_items[0])
                seen = []

                def mutate(msg):
                    msg['content'][0]['CLOSE_PRICE'] = 0

                async def async_mutate(msg):
                    mutate(msg)

                async def observe(msg):
                    seen.append(msg['content'][0]['CLOSE_PRICE'])

                self.client.add_chart_equity_handler(async_mutate if asynchronous else mutate)
                self.client.add_chart_equity_handler(observe)
                await self.client.handle_message()
                await asyncio.gather(*self.client._handler_tasks)
                self.assertEqual(seen, [123.45])
                self.assertEqual(original['data'][0]['content'][0]['5'], 123.45)
                await self.client._close_connection()


class CallbackRoutingTest(unittest.TestCase):
    def test_encoded_paths_match_literal_callback_route(self):
        for path in ('/oauth%20callback', '/caf%C3%A9', '/oauth%2Fcallback',
                     '/oauth;callback', '/oauth%2520callback', '/'):
            with self.subTest(path=path):
                apps = []
                queue = Mock()
                with patch('schwab.auth._run_callback_server',
                           lambda app, port: apps.append(app)):
                    getattr(auth, '__run_client_from_login_flow_server')(
                        queue, 8182, auth._get_callback_path(path), 'ready')
                response = apps[0].test_client().get(
                    path + '?code=test%2Bcode&state=test-state',
                    base_url='https://127.0.0.1:8182')
                self.assertEqual(response.status_code, 200)
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(
                    queue.put.call_args.args[0]).query)
                self.assertEqual(query, {'code': ['test+code'], 'state': ['test-state']})
                self.assertEqual(apps[0].test_client().get('/wrong-path').status_code, 404)

    def test_unsupported_route_paths_are_rejected(self):
        for path in ('/%3Cpath:name%3E', '/a//b', '/%00callback',
                     '/schwab-py-internal/status', '/%FF'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                auth._get_callback_path(path)

    def test_encoded_callback_preserves_original_oauth_redirect(self):
        callback = 'https://127.0.0.1:8182/oauth%20callback;v1'
        received = callback + '?code=test&state=test-state'
        with patch('schwab.auth.multiprocess') as multiprocess, \
                patch('schwab.auth.psutil.Process'), \
                patch('schwab.auth._wait_for_callback_server'), \
                patch('schwab.auth.webbrowser.get'), \
                patch('schwab.auth.get_auth_context') as context, \
                patch('schwab.auth.client_from_received_url') as finish, \
                patch('builtins.print'):
            multiprocess.get_context.return_value = multiprocess
            process = multiprocess.Process
            queue = multiprocess.Queue
            queue.return_value.get.return_value = received
            auth.client_from_login_flow('key', 'secret', callback, 'unused', interactive=False)
        self.assertEqual(process.call_args.kwargs['args'][2], '/oauth callback;v1')
        context.assert_called_once_with('key', callback)
        self.assertIs(finish.call_args.args[2], context.return_value)
        self.assertEqual(finish.call_args.args[3], received)

    def test_invalid_url_forms_are_rejected_before_startup(self):
        for callback in ('http://127.0.0.1:8182',
                         'https://user@127.0.0.1:8182',
                         'https://127.0.0.1:8182/#fragment'):
            with self.subTest(callback=callback), \
                    patch('schwab.auth.multiprocess') as multiprocess:
                with self.assertRaisesRegex(ValueError, 'HTTPS'):
                    auth.client_from_login_flow('key', 'secret', callback, 'unused')
                multiprocess.Process.assert_not_called()
                multiprocess.Queue.assert_not_called()
                multiprocess.get_context.assert_not_called()
