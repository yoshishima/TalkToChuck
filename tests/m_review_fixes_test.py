import asyncio
import datetime
import json
import threading
import traceback
import unittest
import warnings
from unittest.mock import AsyncMock, Mock

from schwab.client import Client
from schwab.orders.generic import OrderBuilder
from schwab.orders.common import OptionInstruction, EquityInstruction, first_triggers_second
from schwab.streaming import StreamClient, StreamBufferOverflowError, UnparsableMessage


class StreamIsolationTest(unittest.IsolatedAsyncioTestCase):
    def make_client(self, frames, **kwargs):
        client = StreamClient(Mock(), **kwargs)
        socket = AsyncMock()
        incoming = asyncio.Queue()
        socket.recv.side_effect = incoming.get
        async def send(raw):
            request = json.loads(raw)['requests'][0]
            for frame in frames:
                incoming.put_nowait(frame)
            incoming.put_nowait(json.dumps({'response': [{
                'requestid': request['requestid'], 'service': request['service'],
                'command': request['command'], 'content': {'code': 0, 'msg': 'ok'}}]}))
        socket.send.side_effect = send
        client._socket = socket
        self.addAsyncCleanup(client._close_connection)
        return client, socket

    async def test_parse_error_preserves_ack_and_message_order(self):
        message = json.dumps({'data': [{'service': 'ACCT_ACTIVITY', 'content': []}]})
        for waiting_consumer in (False, True):
            with self.subTest(waiting_consumer=waiting_consumer):
                client, _ = self.make_client([message, '{broken', message])
                handler = Mock()
                client.add_account_activity_handler(handler)
                consumer = asyncio.create_task(client.handle_message()) if waiting_consumer else None
                await client.level_one_equity_subs(['SPY'])
                if consumer:
                    await consumer
                else:
                    await client.handle_message()
                self.assertEqual(1, handler.call_count)
                with self.assertRaises(UnparsableMessage):
                    await client.handle_message()
                await client.handle_message()
                self.assertEqual(2, handler.call_count)

    async def test_invalid_envelopes_preserve_ack_and_message_order(self):
        bad_frames = ['null', '[]', '{"response":[]}',
                      '{"response":[{"requestid":"bad"}]}',
                      '{"data":null}']
        message = json.dumps({'data': [{'service': 'ACCT_ACTIVITY',
                                        'content': []}]})
        client, _ = self.make_client(bad_frames + [message])
        handler = Mock()
        client.add_account_activity_handler(handler)
        await client.level_one_equity_subs(['SYNTHETIC'])
        for raw in bad_frames:
            with self.subTest(raw=raw):
                with self.assertRaises(UnparsableMessage) as raised:
                    await client.handle_message()
                self.assertEqual(raw, raised.exception.raw_msg)
        await client.handle_message()
        self.assertEqual(1, handler.call_count)

    async def test_parse_error_text_does_not_expose_raw_frame(self):
        raw = '{"accountNumber":"synthetic-private-marker",'
        client, _ = self.make_client([raw])
        await client.level_one_equity_subs(['SYNTHETIC'])
        with self.assertRaises(UnparsableMessage) as raised:
            await client.handle_message()
        self.assertEqual(raw, raised.exception.raw_msg)
        self.assertNotIn('synthetic-private-marker', str(raised.exception))
        self.assertNotIn('synthetic-private-marker', ''.join(
            traceback.format_exception(raised.exception)))

    async def test_bad_entries_do_not_drop_later_data(self):
        client, _ = self.make_client([])
        handler = Mock()
        client.add_account_activity_handler(handler)
        client._overflow_items.append({'data': [None, {}, {'service': []},
            {'service': 'ACCT_ACTIVITY', 'content': []}],
            'notify': [{}, {'service': 'ACCT_ACTIVITY', 'content': []}]})
        with self.assertLogs('schwab.streaming', level='WARNING'):
            await client.handle_message()
        self.assertEqual(2, handler.call_count)

    async def test_buffer_limit_fails_request_and_closes_connection(self):
        message = json.dumps({'data': []})
        client, socket = self.make_client([message] * 4,
            max_pending_messages=3, response_timeout=None)
        with self.assertLogs('schwab.streaming', level='ERROR'):
            with self.assertRaises(StreamBufferOverflowError):
                await client.level_one_equity_subs(['SPY'])
        self.assertIsNone(client._socket)
        self.assertLessEqual(len(client._overflow_items), 3)
        socket.close.assert_awaited_once()

    async def test_parse_errors_also_count_toward_buffer_limit(self):
        client, _ = self.make_client(['{bad'] * 4, max_pending_messages=3)
        with self.assertLogs('schwab.streaming', level='ERROR'):
            with self.assertRaises(StreamBufferOverflowError):
                await client.level_one_equity_subs(['SPY'])

    async def test_ack_at_capacity_is_still_read(self):
        client, _ = self.make_client([json.dumps({'data': []})] * 3, max_pending_messages=3)
        await client.level_one_equity_subs(['SPY'])
        self.assertEqual(3, len(client._overflow_items))

    async def test_invalid_capacity(self):
        for value in (0, -1, True, 1.5, None):
            with self.assertRaises(ValueError):
                StreamClient(Mock(), max_pending_messages=value)

    async def test_opt_in_offloads_synchronous_preferences(self):
        started = threading.Event()
        release = threading.Event()

        def preferences():
            started.set()
            if not release.wait(2):
                raise TimeoutError('test did not release preferences call')
            raise RuntimeError('synthetic failure')

        client = StreamClient(Mock(get_user_preferences=preferences),
                              offload_sync_preferences=True)
        task = asyncio.create_task(client.login())
        try:
            self.assertTrue(await asyncio.wait_for(
                asyncio.to_thread(started.wait, 1), timeout=2))
            self.assertFalse(task.done())
        finally:
            release.set()
        with self.assertRaisesRegex(RuntimeError, 'synthetic failure'):
            await task

    async def test_cancelled_offloaded_lookup_can_finish_in_worker(self):
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def preferences():
            started.set()
            try:
                release.wait(2)
            finally:
                finished.set()

        client = StreamClient(Mock(get_user_preferences=preferences),
                              offload_sync_preferences=True)
        task = asyncio.create_task(client.login())
        try:
            self.assertTrue(await asyncio.wait_for(
                asyncio.to_thread(started.wait, 1), timeout=2))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            release.set()
        self.assertTrue(await asyncio.wait_for(
            asyncio.to_thread(finished.wait, 1), timeout=2))

    async def test_async_preferences_work_with_offloading_enabled(self):
        preferences = AsyncMock(side_effect=RuntimeError('synthetic failure'))
        client = StreamClient(Mock(get_user_preferences=preferences),
                              offload_sync_preferences=True)
        with self.assertRaisesRegex(RuntimeError, 'synthetic failure'):
            await client.login()
        preferences.assert_awaited_once()


class OptionPrecisionTest(unittest.TestCase):
    def setUp(self):
        self.warning_context = warnings.catch_warnings()
        self.warning_context.__enter__()
        warnings.simplefilter('ignore')
        self.addCleanup(self.warning_context.__exit__, None, None, None)

    def option(self, builder=None, quantity=1):
        return (builder if builder is not None else OrderBuilder()).add_option_leg(
            OptionInstruction.BUY_TO_OPEN, 'SPY   261218C00500000', quantity)

    def test_price_order_independence_and_nested_orders(self):
        for field in ('price', 'stop_price'):
            for price_first in (True, False):
                builder = OrderBuilder()
                if not price_first:
                    self.option(builder)
                getattr(builder, 'set_' + field)(0.555)
                if price_first:
                    self.option(builder)
                key = 'stopPrice' if field == 'stop_price' else 'price'
                self.assertEqual('0.55', builder.build()[key])
                parent = first_triggers_second(OrderBuilder(), builder).build()
                self.assertEqual('0.55', parent['childOrderStrategies'][0][key])

    def test_strings_copy_and_equity_precision_unchanged(self):
        builder = self.option().set_price(0.555)
        self.assertEqual('0.55', builder.build()['price'])
        builder.add_equity_leg(EquityInstruction.BUY, 'SPY', 1)
        self.assertEqual('0.5550', builder.build()['price'])
        builder = self.option().set_price(0.555).set_price('0.555')
        self.assertEqual('0.555', builder.build()['price'])
        builder.copy_price(0.555)
        self.assertEqual(0.555, builder.build()['price'])
        builder.clear_price()
        self.assertNotIn('price', builder.build())

    def test_option_price_must_not_become_zero(self):
        with self.assertRaises(ValueError):
            self.option().set_price(0.005).build()
        self.assertEqual('0.00', self.option().set_price(0).build()['price'])

    def test_integral_floats_supported_fractional_rejected(self):
        self.assertEqual(2.0, self.option(quantity=2.0).build()['orderLegCollection'][0]['quantity'])
        for quantity in (0.5, 1.5, True, float('inf'), float('nan')):
            with self.assertRaises(ValueError):
                self.option(quantity=quantity)


class NaiveDatetimeTest(unittest.TestCase):
    def test_warning_and_utc_interpretation(self):
        client = Client('synthetic', Mock())
        value = datetime.datetime(2026, 9, 27, 9, 30)
        with self.assertWarnsRegex(UserWarning, 'interpreted as UTC'):
            result = client._format_date_as_millis('start_datetime', value)
        with warnings.catch_warnings():
            warnings.simplefilter('error')
            self.assertEqual(result, client._format_date_as_millis(
                'start_datetime', value.replace(tzinfo=datetime.timezone.utc)))
