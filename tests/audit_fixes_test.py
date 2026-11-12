'''Regression tests for the 2026-11 audit findings.

Every test is offline and deterministic: synthetic keys, tokens and symbols,
mocked or in-memory transports, and seeded randomness.
'''
import asyncio
import copy
import datetime
import importlib.util
import io
import json
import logging
import os
import random
import tempfile
import threading
import time
import unittest
import warnings
from collections import OrderedDict, deque
from contextlib import redirect_stdout
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import httpx2 as httpx

import schwab
from schwab import auth, debug, streaming
from schwab.client import Client
from schwab.contrib.orders import (
    UnrepeatableOrderError, construct_repeat_order)
from schwab.contrib.util import HeuristicJsonDecoder
from schwab.orders.generic import OrderBuilder
from schwab.scripts import APP_SECRET_ENV_VAR, resolve_app_secret
from schwab.streaming import StreamClient, StreamJsonDecoder, UnparsableMessage
from schwab.utils import LazyLog, Utils

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def mock_client():
    session = MagicMock()
    session.get.return_value = httpx.Response(200, json={})
    return Client('SYNTHETIC-KEY', session), session


def level_one_frame(timestamp, price=101.25):
    return json.dumps({'data': [{
        'service': 'LEVELONE_EQUITIES', 'timestamp': timestamp,
        'command': 'SUBS', 'content': [{'key': 'SYNTH', '1': price}]}]})


# ---------------------------------------------------------------- D1 logging
class LoggingNeverRaisesTest(unittest.IsolatedAsyncioTestCase):

    def test_debug_logging_with_decimal_query_params(self):
        client, session = mock_client()
        logger = schwab.client.base.get_logger()
        old = logger.level
        logger.setLevel(logging.DEBUG)
        try:
            with self.assertLogs(logger, level=logging.DEBUG) as logs:
                client.get_option_chain('SYNTH', strike=Decimal('100.5'))
        finally:
            logger.setLevel(old)
        session.get.assert_called_once()
        self.assertIn('100.5', '\n'.join(logs.output))

    async def test_debug_logging_with_decimal_stream_decoder(self):
        class DecimalDecoder(StreamJsonDecoder):
            def decode_json_string(self, raw):
                return json.loads(raw, parse_float=Decimal)

        stream = StreamClient(Mock())
        stream.set_json_decoder(DecimalDecoder())
        received = []
        stream.add_level_one_equity_handler(received.append)
        socket = AsyncMock()
        socket.recv.return_value = level_one_frame(1)
        stream._socket = socket
        logger = streaming.get_logger()
        old = logger.level
        logger.setLevel(logging.DEBUG)
        try:
            with self.assertLogs(logger, level=logging.DEBUG):
                await stream.handle_message()
        finally:
            logger.setLevel(old)
            await stream._close_connection()
        self.assertEqual(Decimal('101.25'),
                         received[0]['content'][0]['BID_PRICE'])

    def test_lazy_log_failures_are_rendered_not_raised(self):
        def explode():
            raise RuntimeError('synthetic')
        self.assertEqual('<log rendering failed: RuntimeError>',
                         str(LazyLog(explode)))

    def test_redacting_filter_survives_bad_format_arguments(self):
        record = logging.LogRecord(
            'schwab.test', logging.ERROR, __file__, 1, 'value %d', ('x',),
            None)
        self.assertTrue(debug._REDACTING_FILTER.filter(record))
        self.assertEqual('<unformattable log record: TypeError>', record.getMessage())

    def test_redact_json_renders_unserializable_values(self):
        with patch.object(schwab, 'LOG_REDACTOR', debug.LogRedactor()):
            text = debug.redact_json({
                'price': Decimal('1.25'),
                'when': datetime.date(2024, 1, 2),
                'session': schwab.orders.common.Session.NORMAL,
                'other': object()})
        self.assertIn('"1.25"', text)
        self.assertIn('2024-01-02', text)
        self.assertIn('"NORMAL"', text)
        self.assertIn('<object>', text)

    def test_redact_json_never_raises(self):
        cyclic = {'a': []}
        cyclic['a'].append(cyclic)
        self.assertEqual('<unloggable payload: RecursionError>',
                         debug.redact_json(cyclic))

    def test_awaitable_response_bodies_are_omitted(self):
        async def body():
            return {}
        coroutine = body()
        response = Mock()
        response.json.return_value = coroutine
        with patch.object(debug, '_BUG_REPORT_LOGGING_ACTIVE', 1):
            self.assertEqual('<response body omitted>',
                             debug.register_redactions_from_response(
                                 response, return_sanitized=True))
        self.assertIsNone(coroutine.cr_frame)  # closed, not left pending


# ---------------------------------------------------------------- D2 redaction
class RedactionTest(unittest.TestCase):

    def test_placeholders_are_never_redacted_again(self):
        redactor = debug.LogRedactor()
        redactor.register('100', 'chain-2024-01-19-100.0-bid', persistent=False)
        redactor.register('0', 'chain-2024-10-01-0-bidSize', persistent=False)
        redactor.register('1', 'chain-2024-11-01-1-askSize', persistent=False)
        line = 'Req 7: response 200 for 100 contracts at 1.10, 0 or 1'
        redacted = redactor.redact(line)
        # Each original occurrence becomes exactly one placeholder.
        self.assertEqual(redacted.count('<REDACTED'), 3)
        self.assertIn('response 200', redacted)
        self.assertIn('at 1.10', redacted)
        self.assertLess(len(redacted), 20 * len(line))

    def test_option_chain_redaction_stays_bounded(self):
        contracts = {f'{100 + i}.0': [{'symbol': f'SYNTH  240119C{100000 + i:08d}',
                                       'bid': 1.0 + i / 100, 'bidSize': i % 50,
                                       'askMICId': 'XNAS'}]
                     for i in range(200)}
        redactor = debug.LogRedactor()
        debug.register_redactions(
            {'callExpDateMap': {'2024-01-19:30': contracts}},
            redactor=redactor, persistent=False)
        line = json.dumps(contracts)[:2000]
        start = time.perf_counter()
        redacted = redactor.redact(line)
        self.assertLess(time.perf_counter() - start, 1.0)
        self.assertLess(len(redacted), 50 * len(line))

    def test_market_data_numbers_are_not_registered(self):
        redactor = debug.LogRedactor()
        debug.register_redactions(
            {'quote': {'bidPrice': 101.5, 'bidSize': 300, 'midPrice': 101.55,
                       'isShortable': True, 'lastSize': 1}},
            redactor=redactor)
        self.assertEqual({}, redactor.redacted_strings)

    def test_identifiers_and_financial_values_are_still_registered(self):
        redactor = debug.LogRedactor()
        debug.register_redactions(
            {'accountId': 'SYNTH-ACCOUNT-1', 'userid': 'synthuser',
             'liquidationValue': 12345.67, 'bidMICId': 'XNAS',
             'firstName': 'Al'},
            redactor=redactor)
        self.assertEqual(
            {'SYNTH-ACCOUNT-1', 'synthuser', 'XNAS', 'Al'},
            set(redactor.redacted_strings))

    def test_booleans_and_single_characters_are_not_registered(self):
        redactor = debug.LogRedactor()
        debug.register_redactions(
            {'isAuthorized': True, 'tokenType': 'B', 'orderId': 7},
            redactor=redactor)
        self.assertEqual({}, redactor.redacted_strings)

    def test_persistent_tokens_are_bounded_per_label(self):
        redactor = debug.LogRedactor(max_persistent_per_label=4)
        for i in range(10):
            debug.register_redactions(
                {'access_token': f'synthetic-access-{i:04d}',
                 'refresh_token': 'synthetic-refresh'},
                redactor=redactor)
        tokens = sorted(s for s in redactor.redacted_strings
                        if s.startswith('synthetic-access'))
        self.assertEqual([f'synthetic-access-{i:04d}' for i in range(6, 10)],
                         tokens)
        self.assertIn('synthetic-refresh', redactor.redacted_strings)
        self.assertIn('<REDACTED access_token-10>',
                      redactor.redact('synthetic-access-0009'))

    def test_transient_strings_can_become_persistent(self):
        redactor = debug.LogRedactor(max_persistent_per_label=1)
        redactor.register('synthetic-1', 'TOKEN', persistent=False)
        redactor.register('synthetic-1', 'TOKEN')
        redactor.clear_transient()
        self.assertIn('synthetic-1', redactor.redacted_strings)
        redactor.register('synthetic-2', 'TOKEN')
        self.assertNotIn('synthetic-1', redactor.redacted_strings)

    def test_messages_with_private_use_characters(self):
        redactor = debug.LogRedactor()
        redactor.register('secret', 'SECRET')
        self.assertEqual('\U000F0000 <REDACTED SECRET>',
                         redactor.redact('\U000F0000 secret'))


# ---------------------------------------------------------------- D3 refresh
class SyncRefreshSerializationTest(unittest.TestCase):

    def make_token(self):
        return {'creation_timestamp': int(time.time()),
                'token': {'access_token': 'synthetic-old-access',
                          'refresh_token': 'synthetic-refresh',
                          'token_type': 'Bearer', 'expires_in': 1800,
                          'expires_at': int(time.time()) - 10}}

    def run_threads(self, threads, writer=None):
        calls = {'token': 0, 'api': 0}
        lock = threading.Lock()
        writes = []

        def respond(request):
            if request.url.path == '/v1/oauth/token':
                with lock:
                    calls['token'] += 1
                    n = calls['token']
                time.sleep(0.05)
                return httpx.Response(200, json={
                    'access_token': f'synthetic-new-access-{n}',
                    'refresh_token': 'synthetic-refresh',
                    'token_type': 'Bearer', 'expires_in': 1800})
            with lock:
                calls['api'] += 1
            return httpx.Response(200, json=[])

        client = auth.client_from_access_functions(
            'SYNTHETIC-KEY', 'SYNTHETIC-SECRET', self.make_token,
            writer or (lambda token, *args, **kwargs: writes.append(token)))
        client.session._transport = httpx.MockTransport(respond)
        barrier = threading.Barrier(threads)
        errors = []

        def work():
            barrier.wait()
            try:
                client.get_account_numbers()
            except Exception as e:  # pragma: no cover - diagnostic
                errors.append(e)

        workers = [threading.Thread(target=work) for _ in range(threads)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
        client.close_session()
        return client, calls, writes, errors

    def test_threads_share_one_refresh(self):
        for _ in range(5):
            client, calls, writes, errors = self.run_threads(6)
            self.assertEqual([], errors)
            self.assertEqual(1, calls['token'])
            self.assertEqual(6, calls['api'])
            self.assertEqual(1, len(writes))
            self.assertEqual('synthetic-new-access-1',
                             client.token_metadata.token['access_token'])
            self.assertEqual(writes[0]['token']['access_token'],
                             client.token_metadata.token['access_token'])

    def test_token_writer_may_call_the_api(self):
        holder = {}

        def writer(token, *args, **kwargs):
            # Re-entrant use from the refresh callback must not deadlock.
            holder['response'] = holder['client'].get_account_numbers()

        def make_client():
            client = auth.client_from_access_functions(
                'SYNTHETIC-KEY', 'SYNTHETIC-SECRET', self.make_token, writer)
            holder['client'] = client
            return client

        def respond(request):
            if request.url.path == '/v1/oauth/token':
                return httpx.Response(200, json={
                    'access_token': 'synthetic-new-access',
                    'refresh_token': 'synthetic-refresh',
                    'token_type': 'Bearer', 'expires_in': 1800})
            return httpx.Response(200, json=[])

        client = make_client()
        client.session._transport = httpx.MockTransport(respond)
        worker = threading.Thread(target=client.get_account_numbers)
        worker.start()
        worker.join(timeout=10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(200, holder['response'].status_code)
        client.close_session()


# ---------------------------------------------------------------- D4 login
class TokenPathPreflightTest(unittest.TestCase):

    def test_login_flow_checks_token_path_before_starting(self):
        with tempfile.TemporaryDirectory() as directory:
            token_path = os.path.join(directory, 'missing', 'token.json')
            with patch('schwab.auth.multiprocess') as multiprocess, \
                    patch('schwab.auth.OAuth2Client') as oauth:
                with self.assertRaises(FileNotFoundError):
                    auth.client_from_login_flow(
                        'SYNTHETIC-KEY', 'SYNTHETIC-SECRET',
                        'https://127.0.0.1:8182', token_path,
                        interactive=False)
            multiprocess.Process.assert_not_called()
            oauth.assert_not_called()

    def test_manual_flow_checks_token_path_before_prompting(self):
        with tempfile.TemporaryDirectory() as directory:
            token_path = os.path.join(directory, 'missing', 'token.json')
            with patch('schwab.auth.input') as prompt, \
                    patch('schwab.auth.OAuth2Client') as oauth:
                with self.assertRaises(FileNotFoundError):
                    auth.client_from_manual_flow(
                        'SYNTHETIC-KEY', 'SYNTHETIC-SECRET',
                        'https://127.0.0.1:8182', token_path)
            prompt.assert_not_called()
            oauth.assert_not_called()

    def test_custom_token_writers_skip_the_preflight(self):
        with tempfile.TemporaryDirectory() as directory:
            token_path = os.path.join(directory, 'missing', 'token.json')
            with patch('schwab.auth.input', return_value='https://127.0.0.1'), \
                    patch('schwab.auth.get_auth_context',
                          return_value=auth.AuthContext(
                              'https://127.0.0.1:8182', 'https://u', 's')), \
                    patch('schwab.auth.client_from_received_url') as received:
                auth.client_from_manual_flow(
                    'SYNTHETIC-KEY', 'SYNTHETIC-SECRET',
                    'https://127.0.0.1:8182', token_path,
                    token_write_func=Mock())
            received.assert_called_once()

    def test_preflight_leaves_no_files(self):
        with tempfile.TemporaryDirectory() as directory:
            auth._check_token_path_writable(
                os.path.join(directory, 'token.json'))
            self.assertEqual([], os.listdir(directory))

    def test_generate_token_script_reports_the_cause(self):
        spec = importlib.util.spec_from_file_location(
            'schwab_generate_token',
            os.path.join(REPO_ROOT, 'bin', 'schwab-generate-token.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        output = io.StringIO()
        with patch('schwab.auth.client_from_login_flow',
                   side_effect=auth.RedirectServerTimeoutError('no server')), \
                patch('schwab.auth.client_from_manual_flow') as manual, \
                redirect_stdout(output):
            self.assertEqual(0, module.main(
                'SYNTHETIC-KEY', 'SYNTHETIC-SECRET', 'https://127.0.0.1:8182',
                'token.json', None))
        manual.assert_called_once()
        self.assertIn('RedirectServerTimeoutError: no server', output.getvalue())


# ---------------------------------------------------------------- D5 decoders
class DecoderErrorTest(unittest.IsolatedAsyncioTestCase):

    async def test_reader_survives_clearing_a_delivered_parse_error_traceback(self):
        import traceback
        stream = StreamClient(Mock())
        socket = AsyncMock()
        socket.recv.side_effect = [
            '{bad',
            json.dumps({'data': [{'service': 'LEVELONE_EQUITIES',
                                  'content': [{'1': 42}]}]}),
        ]
        stream._socket = socket
        received = []
        stream.add_level_one_equity_handler(received.append)
        try:
            try:
                await stream.handle_message()
            except UnparsableMessage as error:
                frames = traceback.extract_tb(error.__traceback__)
                self.assertNotIn('_reader_loop', [frame.name for frame in frames])
                traceback.clear_frames(error.__traceback__)
            else:
                self.fail('Expected an UnparsableMessage')
            await asyncio.wait_for(stream.handle_message(), timeout=1)
            self.assertEqual(received[0]['content'][0]['BID_PRICE'], 42)
        finally:
            await stream._close_connection()

    async def test_any_decoder_exception_becomes_unparsable_message(self):
        for error in (ValueError('synthetic'), TypeError('synthetic')):
            with self.subTest(error=type(error).__name__):
                class Failing(StreamJsonDecoder):
                    def decode_json_string(self, raw):
                        raise error

                stream = StreamClient(Mock())
                stream.set_json_decoder(Failing())
                socket = AsyncMock()
                socket.recv.return_value = '{}'
                stream._socket = socket
                with self.assertRaises(UnparsableMessage) as raised:
                    await stream.handle_message()
                self.assertIs(error, raised.exception.__cause__)
                await stream._close_connection()

    async def test_heuristic_decoder_accepts_binary_frames(self):
        decoder = HeuristicJsonDecoder()
        self.assertEqual({'a': 1}, decoder.decode_json_string(b'{"a": 1}'))
        stream = StreamClient(Mock())
        stream.set_json_decoder(decoder)
        socket = AsyncMock()
        socket.recv.return_value = b'{"data": ['
        stream._socket = socket
        with self.assertRaises(UnparsableMessage) as raised:
            await stream.handle_message()
        self.assertIsInstance(raised.exception.__cause__, json.JSONDecodeError)
        await stream._close_connection()


# ---------------------------------------------------------------- D6 books
class PartialBookTest(unittest.IsolatedAsyncioTestCase):

    def label(self, message):
        handler = StreamClient._BookHandler(lambda m: None,
                                            StreamClient.BookFields)
        return handler.label_message(message)

    def test_entries_without_content_pass_through(self):
        message = {'service': 'NASDAQ_BOOK', 'timestamp': 1}
        self.assertIs(message, self.label(message))

    def test_partial_levels_are_relabeled_not_dropped(self):
        message = {'service': 'NASDAQ_BOOK', 'content': [
            {'key': 'SYNTH',
             '2': [{'0': 1.0, '1': 100, '2': 1},            # no exchange list
                   'not-a-level',
                   {'0': 0.99, '1': 50, '2': 1, '3': [{'0': 'EX', '1': 50,
                                                      '2': 9}, 'odd']}],
             '3': 'not-a-list'},
            'not-an-entry']}
        labeled = self.label(message)
        bids = labeled['content'][0]['BIDS']
        self.assertEqual({'BID_PRICE': 1.0, 'TOTAL_VOLUME': 100, 'NUM_BIDS': 1},
                         bids[0])
        self.assertEqual('not-a-level', bids[1])
        self.assertEqual({'EXCHANGE': 'EX', 'BID_VOLUME': 50, 'SEQUENCE': 9},
                         bids[2]['BIDS'][0])
        self.assertEqual('not-a-list', labeled['content'][0]['ASKS'])
        self.assertEqual('not-an-entry', labeled['content'][1])
        self.assertEqual({'0': 1.0, '1': 100, '2': 1},
                         message['content'][0]['2'][0])


# ---------------------------------------------------------------- D7, D8, D11
class StreamRequestValidationTest(unittest.IsolatedAsyncioTestCase):

    async def sent_parameters(self, stream, coroutine):
        sent = []

        async def fake_send(request, request_id, service, command, **kwargs):
            sent.append(request['parameters'])
        stream._send_request_and_await_response = fake_send
        stream._socket = AsyncMock()
        await coroutine(stream)
        return sent[0]

    async def test_empty_field_list_is_rejected(self):
        stream = StreamClient(Mock())
        stream._socket = AsyncMock()
        with self.assertRaisesRegex(ValueError, 'fields must not be empty'):
            await stream.level_one_equity_subs(['SYNTH'], fields=[])

    async def test_required_field_is_not_duplicated(self):
        stream = StreamClient(Mock(), enforce_enums=False)
        parameters = await self.sent_parameters(
            stream, lambda s: s.level_one_equity_subs(['SYNTH'], fields=[1, 0]))
        self.assertEqual('0,1', parameters['fields'])

    async def test_account_activity_before_login(self):
        stream = StreamClient(Mock())
        for operation in (stream.account_activity_sub,
                          stream.account_activity_unsubs):
            with self.subTest(operation=operation.__name__):
                with self.assertRaisesRegex(ValueError, 'Socket not open'):
                    await operation()

    def test_offload_flag_must_be_boolean(self):
        with self.assertRaisesRegex(ValueError, 'offload_sync_preferences'):
            StreamClient(Mock(), offload_sync_preferences='yes')

    async def test_logout_before_login(self):
        stream = StreamClient(Mock())
        with self.assertRaisesRegex(ValueError, 'Socket not open'):
            await stream.logout()

    def test_response_timeout_validation(self):
        for value in (-1, 0, '30', float('nan'), float('inf'), True,
                      Decimal('1')):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    StreamClient(Mock(), response_timeout=value)
        for value in (None, 1, 0.5):
            StreamClient(Mock(), response_timeout=value)

    async def test_login_rejects_non_200_success_codes(self):
        client = Mock()
        client.get_user_preferences.return_value = httpx.Response(
            204, request=httpx.Request(
                'GET', 'https://api.schwabapi.com/trader/v1/userPreference'))
        stream = StreamClient(client)
        with self.assertRaisesRegex(httpx.HTTPStatusError, '204'):
            await stream.login()


# ---------------------------------------------------------------- D9, R2
class OrderInputValidationTest(unittest.TestCase):

    def test_invalid_price_types(self):
        for value in (True, None, object(), [1]):
            for setter in ('set_price', 'set_stop_price'):
                with self.subTest(value=value, setter=setter):
                    with warnings.catch_warnings(record=True) as caught:
                        warnings.simplefilter('always')
                        with self.assertRaisesRegex(ValueError, 'price must be'):
                            getattr(OrderBuilder(), setter)(value)
                    self.assertEqual([], caught)

    def test_non_finite_prices(self):
        for value in (float('nan'), float('inf'), Decimal('NaN'),
                      Decimal('-Infinity')):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, 'finite'):
                    OrderBuilder().set_price(value)

    def test_zero_and_negative_zero_prices_are_allowed(self):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            self.assertEqual('0.00', OrderBuilder().set_price(-0.0).build()['price'])
        self.assertEqual('0.00', OrderBuilder().set_price(Decimal('0')).build()['price'])

    def test_truncate_float_still_rejects_non_finite_values(self):
        from schwab.orders.generic import truncate_float
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            with self.assertRaisesRegex(ValueError, 'finite'):
                truncate_float(float('nan'))

    def test_unsupported_values_in_dict_orders(self):
        for value, name in ((None, 'NoneType'), ((1, 2), 'tuple'),
                            (object(), 'object'), (float('nan'), 'finite')):
            with self.subTest(value=value):
                builder = OrderBuilder().add_child_order_strategy(
                    {'orderType': 'LIMIT', 'nested': {'price': value}})
                with self.assertRaisesRegex(ValueError, name) as raised:
                    builder.build()
                if name != 'finite':
                    self.assertIn('nested.price', str(raised.exception))

    def test_self_referencing_order(self):
        builder = OrderBuilder()
        builder.add_child_order_strategy(builder)
        with self.assertRaisesRegex(ValueError, 'contains itself'):
            builder.build()

    def test_shared_child_is_not_a_cycle(self):
        child = {'orderType': 'MARKET'}
        built = (OrderBuilder().add_child_order_strategy(child)
                 .add_child_order_strategy(child).build())
        self.assertEqual([child, child], built['childOrderStrategies'])


# ---------------------------------------------------------------- D10
class CorruptTokenTest(unittest.TestCase):

    def load(self, content):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'token.json')
            with open(path, 'w') as f:
                f.write(content)
            return auth.client_from_token_file(
                path, 'SYNTHETIC-KEY', 'SYNTHETIC-SECRET')

    def test_corrupted_shapes_raise_clear_errors(self):
        cases = {
            '"creation_timestamp text"': 'must be a JSON object',
            '42': 'must be a JSON object',
            '[]': 'must be a JSON object',
            '{"token": {}}': 'format has changed',
            '{"creation_timestamp": "yesterday", "token": {}}':
                'creation_timestamp must be a number',
            '{"creation_timestamp": true, "token": {}}':
                'creation_timestamp must be a number',
            '{"creation_timestamp": 1, "token": "text"}':
                'missing its OAuth token object',
        }
        for content, message in cases.items():
            with self.subTest(content=content):
                with self.assertRaisesRegex(ValueError, message) as raised:
                    self.load(content)
                self.assertNotIn(content, str(raised.exception))


# ---------------------------------------------------------------- D12
class FieldMappingCacheTest(unittest.TestCase):

    def test_base_class_cache_does_not_leak_into_subclasses(self):
        enum_class = StreamClient.LevelOneForexFields
        enum_class.__dict__.get('_key_mapping') and delattr(
            enum_class, '_key_mapping')
        self.assertEqual({}, streaming._BaseFieldEnum.key_mapping())
        try:
            self.assertEqual(len(enum_class), len(enum_class.key_mapping()))
        finally:
            delattr(streaming._BaseFieldEnum, '_key_mapping')


# ---------------------------------------------------------------- D13, M4
class EasyClientTest(unittest.TestCase):

    @patch('schwab.auth.client_from_token_file')
    @patch('schwab.auth.client_from_login_flow')
    @patch('schwab.auth.os.path.isfile', return_value=True)
    def test_aged_client_is_closed_and_log_text_is_correct(
            self, isfile, login_flow, token_file):
        old_client = token_file.return_value
        old_client.token_age.return_value = 10_000
        with self.assertLogs('schwab.auth', level=logging.INFO) as logs:
            auth.easy_client('SYNTHETIC-KEY', 'SYNTHETIC-SECRET',
                             'https://127.0.0.1:8182', 'token.json',
                             max_token_age=100)
        old_client.session.close.assert_called_once_with()
        self.assertTrue(any('writing token to' in line for line in logs.output))

    @patch('schwab.auth.client_from_token_file')
    @patch('schwab.auth.client_from_login_flow')
    @patch('schwab.auth.os.path.isfile', return_value=True)
    def test_aged_async_client_is_not_closed_synchronously(
            self, isfile, login_flow, token_file):
        old_client = token_file.return_value
        old_client.token_age.return_value = 10_000
        auth.easy_client('SYNTHETIC-KEY', 'SYNTHETIC-SECRET',
                         'https://127.0.0.1:8182', 'token.json',
                         asyncio=True, max_token_age=100)
        old_client.session.close.assert_not_called()
        login_flow.assert_called_once()

    def test_temporary_oauth_clients_are_closed(self):
        with patch('schwab.auth.OAuth2Client') as oauth:
            oauth.return_value.create_authorization_url.return_value = (
                'https://synthetic.invalid', 'state')
            auth.get_auth_context('SYNTHETIC-KEY', 'https://127.0.0.1:8182')
            oauth.return_value.close.assert_called_once_with()

        with patch('schwab.auth.OAuth2Client') as oauth, \
                patch('schwab.auth._client_from_token_metadata'):
            oauth.return_value.fetch_token.return_value = {
                'access_token': 'synthetic-access'}
            auth.client_from_received_url(
                'SYNTHETIC-KEY', 'SYNTHETIC-SECRET',
                auth.AuthContext('https://127.0.0.1:8182', 'u', 'state'),
                'https://127.0.0.1:8182/?code=c&state=state', Mock())
            oauth.return_value.close.assert_called_once_with()

    def test_sync_client_close_session(self):
        session = MagicMock()
        Client('SYNTHETIC-KEY', session).close_session()
        session.close.assert_called_once_with()


# ---------------------------------------------------------------- D14
class RepeatOrderFidelityTest(unittest.TestCase):

    def order(self, **leg_fields):
        leg = {'orderLegType': 'EQUITY', 'legId': 1, 'instruction': 'BUY',
               'positionEffect': 'OPENING', 'quantity': 100.0,
               'instrument': {'assetType': 'EQUITY', 'symbol': 'SYNTH',
                              'cusip': '000000000'}}
        leg.update(leg_fields)
        return {'session': 'NORMAL', 'duration': 'DAY', 'orderType': 'MARKET',
                'orderStrategyType': 'SINGLE', 'orderLegCollection': [leg]}

    def test_non_share_quantities_are_refused(self):
        for quantity_type in ('DOLLARS', 'ALL_SHARES'):
            with self.subTest(quantity_type=quantity_type):
                with self.assertRaisesRegex(UnrepeatableOrderError,
                                            'quantityType'):
                    construct_repeat_order(self.order(quantityType=quantity_type))

    def test_share_quantities_and_null_fields_are_repeated(self):
        for fields in ({}, {'quantityType': 'SHARES'},
                       {'divCapGains': None, 'toSymbol': None}):
            with self.subTest(fields=fields):
                built = construct_repeat_order(self.order(**fields)).build()
                self.assertEqual(100.0, built['orderLegCollection'][0]['quantity'])

    def test_unmodeled_leg_fields_are_refused(self):
        with self.assertRaisesRegex(UnrepeatableOrderError, 'divCapGains'):
            construct_repeat_order(self.order(divCapGains='REINVEST'))

    def test_unknown_instruction(self):
        with self.assertRaisesRegex(UnrepeatableOrderError, 'instruction'):
            construct_repeat_order(self.order(instruction='EXCHANGE'))

    @patch('schwab.scripts.orders_codegen.client_from_token_file')
    def test_codegen_cli_reports_refusal(self, token_file):
        from schwab.scripts.orders_codegen import latest_order_main
        order = self.order(quantityType='DOLLARS')
        order.update(orderId=1, enteredTime='2024-01-02T03:04:05+0000')
        token_file.return_value.get_orders_for_all_linked_accounts.return_value = (
            httpx.Response(200, json=[order]))
        output = io.StringIO()
        with redirect_stdout(output):
            result = latest_order_main([
                '--token_file', 'token.json', '--api_key', 'SYNTHETIC-KEY',
                '--app_secret', 'SYNTHETIC-SECRET'])
        self.assertEqual(-1, result)
        self.assertIn('Cannot generate code for order 1', output.getvalue())


# ---------------------------------------------------------------- R3
class NaiveDatetimeWarningTest(unittest.TestCase):

    def test_order_and_transaction_queries_warn_for_naive_datetimes(self):
        client, _ = mock_client()
        naive = datetime.datetime(2024, 1, 2, 3, 4, 5)
        calls = (
            lambda: client.get_orders_for_account(
                'SYNTHHASH', from_entered_datetime=naive),
            lambda: client.get_orders_for_all_linked_accounts(
                to_entered_datetime=naive),
            lambda: client.get_transactions('SYNTHHASH', start_date=naive),
        )
        for call in calls:
            with self.subTest(call=call):
                with self.assertWarnsRegex(UserWarning, 'interpreted as UTC') as cm:
                    call()
                self.assertEqual(__file__, cm.filename)

    def test_aware_datetimes_and_dates_do_not_warn(self):
        client, _ = mock_client()
        aware = datetime.datetime(2024, 1, 2, tzinfo=datetime.timezone.utc)
        with warnings.catch_warnings():
            warnings.simplefilter('error')
            client.get_orders_for_account('SYNTHHASH', from_entered_datetime=aware)
            client.get_transactions('SYNTHHASH', start_date=datetime.date(2024, 1, 2))


# ---------------------------------------------------------------- R4
class CallbackStateFilterTest(unittest.TestCase):

    def make_app(self, expected_state):
        apps = []
        queue = Mock()
        with patch('schwab.auth._run_callback_server',
                   lambda app, port: apps.append(app)):
            getattr(auth, '__run_client_from_login_flow_server')(
                queue, 8182, '/callback', 'ready', expected_state)
        return apps[0].test_client(), queue

    def test_requests_without_matching_state_are_ignored(self):
        client, queue = self.make_app('expected-state')
        for query in ('', '?code=c', '?code=c&state=other-state'):
            with self.subTest(query=query):
                self.assertEqual(400, client.get('/callback' + query).status_code)
        queue.put.assert_not_called()
        self.assertEqual(
            200, client.get('/callback?code=c&state=expected-state').status_code)
        queue.put.assert_called_once()

    def test_no_expected_state_accepts_any_request(self):
        client, queue = self.make_app(None)
        self.assertEqual(200, client.get('/callback?code=c').status_code)
        queue.put.assert_called_once()


# ---------------------------------------------------------------- S1, S2
class HardeningTest(unittest.TestCase):

    def test_directory_sync_is_best_effort(self):
        with tempfile.TemporaryDirectory() as directory:
            auth._fsync_directory(directory)
            auth._fsync_directory(os.path.join(directory, 'missing'))

    @unittest.skipUnless(os.name == 'posix', 'directory fsync is POSIX-only')
    def test_token_write_syncs_the_directory(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch('schwab.auth._fsync_directory') as sync:
            path = os.path.join(directory, 'token.json')
            getattr(auth, '__make_update_token_func')(path)({'token': {}})
        sync.assert_called_once_with(os.path.realpath(directory))

    def test_app_secret_precedence(self):
        with patch.dict(os.environ, {APP_SECRET_ENV_VAR: 'from-env'}), \
                patch('schwab.scripts.getpass.getpass') as prompt:
            self.assertEqual('from-arg', resolve_app_secret('from-arg'))
            self.assertEqual('from-env', resolve_app_secret(None))
            prompt.assert_not_called()
        with patch.dict(os.environ, {}, clear=True), \
                patch('schwab.scripts.getpass.getpass',
                      return_value='from-prompt') as prompt:
            self.assertEqual('from-prompt', resolve_app_secret(None))
            prompt.assert_called_once()


# ---------------------------------------------------------------- M1 coverage
class PreviouslyUntestedPathsTest(unittest.TestCase):

    def test_contextual_enum_lookup_by_api_value(self):
        Period = Client.PriceHistory.Period
        self.assertIs(Period.ONE_DAY, Period(1))
        with self.assertRaises(ValueError):
            Period(7)

    def test_set_account_hash(self):
        utils = Utils(Mock(), 'first')
        utils.set_account_hash('second')
        self.assertEqual('second', utils.account_hash)

    def test_option_symbol_error_paths(self):
        from schwab.orders.options import OptionSymbol
        with self.assertRaisesRegex(ValueError, 'Contract type'):
            OptionSymbol('SYNTH', '240119', 'X', '100')
        with self.assertRaisesRegex(ValueError, 'expiration_date must'):
            OptionSymbol('SYNTH', 240119, 'C', '100')
        with self.assertRaisesRegex(ValueError, 'expiration date must follow'):
            OptionSymbol('SYNTH', '2401xx', 'C', '100')
        symbol = OptionSymbol(
            'SYNTH', datetime.datetime(2024, 1, 19, 15, 30), 'P', '100')
        self.assertEqual('SYNTH 240119P00100000', symbol.build())


# ---------------------------------------------------------------- P1 copies
def reference_label(field_enum, message):
    '''The pre-optimization implementation: deep copy, then relabel.'''
    new_message = copy.deepcopy(message)
    for index in range(len(message['content'])):
        field_enum.relabel_message(message['content'][index],
                                   new_message['content'][index])
    return new_message


def ordered(value):
    if isinstance(value, dict):
        return [type(value).__name__] + [(k, ordered(v)) for k, v in value.items()]
    if isinstance(value, list):
        return ['list'] + [ordered(v) for v in value]
    return (type(value).__name__, value)


def containers(value, found=None):
    found = set() if found is None else found
    if isinstance(value, (dict, list)):
        found.add(id(value))
        for item in (value.values() if isinstance(value, dict) else value):
            containers(item, found)
    return found


def random_json(rng, depth=0):
    kind = rng.randrange(8 if depth < 3 else 5)
    if kind == 0:
        return None
    if kind == 1:
        return rng.random() < 0.5
    if kind == 2:
        return rng.randrange(-10**6, 10**6)
    if kind == 3:
        return rng.uniform(-1e6, 1e6)
    if kind == 4:
        return ''.join(rng.choice('abcXYZ019 _') for _ in range(rng.randrange(6)))
    if kind == 5:
        return [random_json(rng, depth + 1) for _ in range(rng.randrange(4))]
    keys = [str(rng.randrange(60)) for _ in range(rng.randrange(5))]
    keys += rng.sample(['key', 'delayed', 'BID_PRICE', 'x'], rng.randrange(3))
    return {k: random_json(rng, depth + 1) for k in keys}


class StreamCopyEquivalenceTest(unittest.IsolatedAsyncioTestCase):

    def test_labels_match_reference_implementation(self):
        rng = random.Random(20261104)
        handler = streaming._Handler(lambda m: None,
                                     StreamClient.LevelOneEquityFields)
        for _ in range(500):
            entries = []
            for _ in range(rng.randrange(5)):
                entry = random_json(rng, depth=1)
                entries.append(entry if isinstance(entry, dict) else {'1': entry})
            message = {'service': 'LEVELONE_EQUITIES', 'timestamp': 1,
                       'extra': random_json(rng, depth=2), 'content': entries}
            snapshot = json.loads(json.dumps(message))
            labeled = handler.label_message(message)
            self.assertEqual(ordered(reference_label(
                StreamClient.LevelOneEquityFields, snapshot)), ordered(labeled))
            self.assertEqual(snapshot, message)
            self.assertFalse(containers(labeled) & containers(message))

    def test_key_collisions_match_reference(self):
        handler = streaming._Handler(lambda m: None,
                                     StreamClient.LevelOneEquityFields)
        for entry in ({'1': 5, 'BID_PRICE': 7}, {'BID_PRICE': 7, '1': 5},
                      {'key': 'S', '2': 1, 'z': 0, '1': 3}):
            message = {'content': [entry]}
            self.assertEqual(
                ordered(reference_label(StreamClient.LevelOneEquityFields,
                                        copy.deepcopy(message))),
                ordered(handler.label_message(message)))

    def test_custom_container_types_keep_deepcopy_semantics(self):
        handler = streaming._Handler(lambda m: None,
                                     StreamClient.LevelOneEquityFields)
        price = Decimal('1.25')
        message = {'content': [OrderedDict([('1', price)])]}
        labeled = handler.label_message(message)
        self.assertIsInstance(labeled['content'][0], OrderedDict)
        self.assertIs(price, labeled['content'][0]['BID_PRICE'])
        ordered_message = OrderedDict([('content', [{'1': 1.0}])])
        self.assertIsInstance(handler.label_message(ordered_message), OrderedDict)

    def test_cyclic_messages_fall_back_to_deepcopy(self):
        message = {'service': 'X', 'content': [{'1': 1.0}]}
        message['content'][0]['self'] = message
        handler = streaming._Handler(lambda m: None,
                                     StreamClient.LevelOneEquityFields)
        labeled = handler.label_message(message)
        self.assertIs(labeled, labeled['content'][0]['self'])
        clone = streaming._copy_message(message)
        self.assertIs(clone, clone['content'][0]['self'])

    async def test_book_handlers_receive_independent_nested_levels(self):
        frame = json.dumps({'data': [{'service': 'NASDAQ_BOOK', 'content': [
            {'key': 'SYNTH', '1': 1,
             '2': [{'0': 1.0, '1': 10, '2': 1, '3': [{'0': 'EX', '1': 10, '2': 1}]}],
             '3': [{'0': 1.1, '1': 20, '2': 1, '3': [{'0': 'EX', '1': 20, '2': 2}]}]}]}]})
        for asynchronous in (False, True):
            with self.subTest(asynchronous=asynchronous):
                stream = StreamClient(Mock())
                socket = AsyncMock()
                socket.recv.return_value = frame
                stream._socket = socket
                seen = []

                def mutate(message):
                    level = message['content'][0]['BIDS'][0]
                    level['BIDS'][0]['BID_VOLUME'] = -1
                    message['content'][0]['ASKS'].clear()

                async def async_mutate(message):
                    mutate(message)

                def observe(message):
                    entry = message['content'][0]
                    seen.append((entry['BIDS'][0]['BIDS'][0]['BID_VOLUME'],
                                 len(entry['ASKS'])))

                stream.add_nasdaq_book_handler(
                    async_mutate if asynchronous else mutate)
                stream.add_nasdaq_book_handler(observe)
                stream.add_nasdaq_book_handler(lambda message: observe(message))
                await stream.handle_message()
                await asyncio.gather(*tuple(stream._handler_tasks))
                self.assertEqual([(10, 1), (10, 1)], seen)
                await stream._close_connection()


# ---------------------------------------------------------------- M6
class FakeSocket:
    '''In-memory socket: preloaded frames, acknowledgements inserted at
    seeded random positions, and recv() that waits when empty.'''

    def __init__(self, frames, rng):
        self.frames = deque(frames)
        self.rng = rng
        self.available = asyncio.Event()
        self.available.set()

    async def recv(self):
        while not self.frames:
            self.available.clear()
            await self.available.wait()
        return self.frames.popleft()

    async def send(self, data):
        request = json.loads(data)['requests'][0]
        ack = json.dumps({'response': [{
            'service': request['service'], 'command': request['command'],
            'requestid': request['requestid'],
            'content': {'code': 0, 'msg': 'ok'}}]})
        self.frames.insert(self.rng.randrange(len(self.frames) + 1), ack)
        self.available.set()

    async def close(self):
        pass


class StreamInterleavingTest(unittest.IsolatedAsyncioTestCase):
    '''Seeded interleavings of data, malformed frames, acknowledgements,
    consumer cancellation and handler backpressure. Oracle: every data entry
    reaches the synchronous handler exactly once and in order, every malformed
    frame is reported once in order, every asynchronous handler runs exactly
    once, and every subscription completes.'''

    async def run_seed(self, seed):
        rng = random.Random(seed)
        frames, expected = [], []
        for timestamp in range(40):
            if rng.random() < 0.15:
                frames.append('{malformed')
                expected.append('bad')
            frames.append(level_one_frame(timestamp))
            expected.append(timestamp)
        stream = StreamClient(Mock(), max_pending_handler_tasks=2,
                              response_timeout=5)
        stream._socket = FakeSocket(frames, rng)
        events, async_seen = [], []

        def record(message):
            events.append(message['timestamp'])

        async def slow(message):
            await asyncio.sleep(rng.choice((0, 0, 0.001)))
            async_seen.append(message['timestamp'])

        stream.add_level_one_equity_handler(record)
        stream.add_level_one_equity_handler(slow)

        async def consume():
            while len(events) < len(expected):
                timeout = rng.choice((0, 0, 0.0005, 1))
                try:
                    await asyncio.wait_for(stream.handle_message(), timeout)
                except TimeoutError:
                    pass
                except UnparsableMessage:
                    events.append('bad')

        async def subscribe():
            for _ in range(4):
                await asyncio.sleep(rng.choice((0, 0.0005)))
                await stream.level_one_equity_add(['SYNTH'])

        async with asyncio.timeout(20):
            await asyncio.gather(consume(), subscribe())
            await asyncio.gather(*tuple(stream._handler_tasks))
        await stream._close_connection()
        return expected, events, async_seen, stream

    async def test_seeded_interleavings(self):
        for seed in range(25):
            with self.subTest(seed=seed):
                expected, events, async_seen, stream = await self.run_seed(seed)
                self.assertEqual(expected, events)
                self.assertEqual(
                    sorted(e for e in expected if e != 'bad'), sorted(async_seen))
                self.assertEqual(set(), stream._handler_tasks)
                self.assertIsNone(stream._reader_task)


# ---------------------------------------------------------------- M8
class PackagingConsistencyTest(unittest.TestCase):

    def install_requires(self):
        import ast
        with open(os.path.join(REPO_ROOT, 'setup.py'), encoding='utf-8') as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == 'install_requires':
                return ast.literal_eval(node.value)
        raise AssertionError('install_requires not found')

    def floors(self, requirements):
        result = {}
        for requirement in requirements:
            name, _, version = requirement.partition('>=')
            result[name.strip().lower()] = version.strip()
        return result

    def test_docs_requirements_match_setup_floors(self):
        setup_floors = self.floors(self.install_requires())
        with open(os.path.join(REPO_ROOT, 'docs', 'requirements.txt')) as f:
            lines = [line.strip() for line in f
                     if line.strip() and not line.startswith('#')]
        for name, version in self.floors(lines).items():
            if name in setup_floors:
                with self.subTest(package=name):
                    self.assertEqual(setup_floors[name], version)

    def test_installed_runtime_dependencies_satisfy_checkout(self):
        from importlib.metadata import version
        from packaging.requirements import Requirement

        for text in self.install_requires():
            requirement = Requirement(text)
            with self.subTest(package=requirement.name):
                self.assertIn(version(requirement.name), requirement.specifier)

    def test_readme_names_the_current_version(self):
        with open(os.path.join(REPO_ROOT, 'README.md'), encoding='utf-8') as f:
            self.assertIn('v' + schwab.__version__, f.read())

    def test_unused_runtime_dependencies_stay_removed(self):
        names = set(self.floors(self.install_requires()))
        self.assertFalse({'certifi', 'urllib3'} & names)
