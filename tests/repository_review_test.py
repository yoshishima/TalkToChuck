import copy
import io
import json
import logging
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx2 as httpx
import schwab
from schwab import auth, debug, streaming
from schwab.client import AsyncClient, Client
from schwab.utils import Utils


class LoginRefreshTest(unittest.IsolatedAsyncioTestCase):
    async def test_new_login_refreshes_and_persists_for_both_clients(self):
        for asynchronous in (False, True):
            with self.subTest(asynchronous=asynchronous):
                expired = {'access_token': 'expired-access',
                           'refresh_token': 'initial-refresh',
                           'token_type': 'Bearer', 'expires_at': 1}
                refreshed = {'access_token': 'refreshed-access',
                             'refresh_token': 'refreshed-refresh',
                             'token_type': 'Bearer', 'expires_in': 1800}
                writer = Mock()
                with patch.object(auth.OAuth2Client, 'fetch_token',
                                  return_value=expired):
                    client = auth.client_from_received_url(
                        'client-key', 'client-secret',
                        auth.AuthContext('https://127.0.0.1:8182', '', 'state'),
                        'https://127.0.0.1:8182?code=test&state=state', writer,
                        asyncio=asynchronous)

                def respond(request):
                    if request.url.path == '/v1/oauth/token':
                        self.assertEqual('POST', request.method)
                        self.assertIn(
                            b'grant_type=refresh_token', request.content)
                        return httpx.Response(200, json=refreshed)
                    self.assertEqual('Bearer refreshed-access',
                                     request.headers['Authorization'])
                    return httpx.Response(200, json={'accounts': []})

                # Exercise real Authlib refresh behavior without network.
                with patch.object(client.session, '_transport',
                                  httpx.MockTransport(respond)):
                    try:
                        response = client.get_accounts()
                        if asynchronous:
                            response = await response
                        self.assertEqual(200, response.status_code)
                        self.assertEqual(2, writer.call_count)
                        persisted = writer.call_args.args[0]
                        self.assertEqual('refreshed-access',
                                         persisted['token']['access_token'])
                        first_write = writer.call_args_list[0].args[0]
                        self.assertEqual(first_write['creation_timestamp'],
                                         persisted['creation_timestamp'])
                        self.assertEqual('refreshed-access',
                                         client.token_metadata.token[
                                             'access_token'])
                    finally:
                        if asynchronous:
                            await client.session.aclose()
                        else:
                            client.session.close()


class LoggingPrivacyTest(unittest.IsolatedAsyncioTestCase):
    async def test_sensitive_arrays_inherit_their_field_names(self):
        payload = {'accountNumbers': ['private-first', ['private-second']],
                   'requestId': ['public-request']}
        with patch.object(schwab, 'LOG_REDACTOR', debug.LogRedactor()):
            sanitized = debug.redact_json(payload)
            self.assertNotIn('private-first', sanitized)
            self.assertNotIn('private-second', sanitized)
            self.assertIn('public-request', sanitized)
            debug.register_redactions(payload)
            self.assertEqual('<REDACTED accountNumbers-0>',
                             schwab.LOG_REDACTOR.redact('private-first'))
            self.assertEqual('private-first', payload['accountNumbers'][0])

    async def test_request_identifiers_and_escaped_secrets_are_redacted(self):
        secret = 'private-"value\\\u00e9'
        for client_type in (Client, AsyncClient):
            for method in ('get', 'post', 'put', 'delete'):
                with self.subTest(client=client_type, method=method), \
                        patch.object(schwab, 'LOG_REDACTOR',
                                     debug.LogRedactor()):
                    session = Mock()
                    response = httpx.Response(200, json={})
                    request_method = (AsyncMock(return_value=response)
                                      if client_type is AsyncClient
                                      else Mock(return_value=response))
                    setattr(session, method, request_method)
                    client = client_type('client-key', session)
                    schwab.LOG_REDACTOR.register(secret, 'SECRET')
                    path = '/trader/v1/accounts/private-account/orders/123456'
                    payload = {'note': secret, 'accountNumber': 'new-account'}
                    with self.assertLogs(level=logging.DEBUG) as logs:
                        args = ((path,) if method == 'delete'
                                else (path, payload))
                        request = getattr(client, '_' + method + '_request')
                        result = request(*args)
                        if client_type is AsyncClient:
                            await result
                    output = '\n'.join(logs.output)
                    for value in ('private-account', '123456', 'new-account',
                                  secret, json.dumps(secret)[1:-1]):
                        self.assertNotIn(value, output)
                    self.assertEqual('https://api.schwabapi.com' + path,
                                     request_method.call_args.args[0])
                    if method != 'delete':
                        self.assertEqual(payload, next(iter(
                            request_method.call_args.kwargs.values())))

    async def test_failed_writer_cannot_log_unredacted_refresh_tokens(self):
        for asynchronous in (False, True):
            with self.subTest(asynchronous=asynchronous), \
                    patch.object(schwab, 'LOG_REDACTOR', debug.LogRedactor()):
                token = {'access_token': 'fresh-access-secret',
                         'refresh_token': 'fresh-refresh-secret'}
                logger = debug.get_redacted_logger('schwab.test.refresh')

                def writer(payload):
                    logger.debug('Token: %s', payload)
                    raise PermissionError('write failed')

                metadata = auth.TokenMetadata({}, 123, writer)
                with self.assertLogs(level=logging.DEBUG) as logs:
                    with self.assertRaises(PermissionError):
                        if asynchronous:
                            writer_callback = (
                                metadata.async_wrapped_token_write_func())
                            await writer_callback(token)
                        else:
                            metadata.wrapped_token_write_func()(token)
                self.assertNotIn('fresh-access-secret', '\n'.join(logs.output))
                self.assertNotIn('fresh-refresh-secret', '\n'.join(logs.output))

    async def test_tracebacks_and_stack_info_redact_registered_secrets(self):
        output = io.StringIO()
        logger = debug.get_redacted_logger('schwab.test.traceback')
        handler = logging.StreamHandler(output)
        logger.addHandler(handler)
        try:
            with patch.object(schwab, 'LOG_REDACTOR', debug.LogRedactor()), \
                    patch.object(logger, 'level', logging.DEBUG):
                schwab.LOG_REDACTOR.register('private-credential', 'SECRET')
                try:
                    raise ValueError('failed for private-credential')
                except ValueError:
                    logger.exception('Request failed')
                logger.handle(logging.LogRecord(
                    logger.name, logging.ERROR, __file__, 1, 'Stack', (), None,
                    sinfo='stack contains private-credential'))
            self.assertNotIn('private-credential', output.getvalue())
            self.assertIn('ValueError: failed for <REDACTED SECRET>',
                          output.getvalue())
        finally:
            logger.removeHandler(handler)


class OrderLocationTest(unittest.TestCase):
    def test_malformed_locations_do_not_return_a_partial_order_id(self):
        utils = Utils(Mock(), 'account_hash')
        path = '/trader/v1/accounts/account_hash/orders/'
        for location in (
                'https://apiXschwabapiYcom' + path + '123',
                'https://api.schwabapi.com' + path + '123evil',
                'https://api.schwabapi.com' + path + '123/456'):
            with self.subTest(location=location):
                self.assertIsNone(utils.extract_order_id(httpx.Response(
                    201, headers={'Location': location})))

    def test_hyphenated_account_hash_matches_client_identifier_rules(self):
        utils = Utils(Mock(), 'account-hash')
        location = ('https://api.schwabapi.com/trader/v1/accounts/'
                    'account-hash/orders/123')
        self.assertEqual(123, utils.extract_order_id(httpx.Response(
            201, headers={'Location': location})))


class StreamDispatchOwnershipTest(unittest.IsolatedAsyncioTestCase):
    async def test_last_handler_preserves_input_and_other_handlers(self):
        for handler_count in (1, 3):
            with self.subTest(handlers=handler_count):
                client = streaming.StreamClient(Mock())
                raw = {'service': 'LEVELONE_EQUITIES',
                       'content': [{'key': 'AAPL', '1': 123,
                                    'nested': {'values': [1, 2]}}]}
                original = copy.deepcopy(raw)
                received = []
                for index in range(handler_count):
                    def handler(message, index=index):
                        received.append(copy.deepcopy(message))
                        message['content'][0]['nested']['values'].append(index)
                    client.add_level_one_equity_handler(handler)
                pending = streaming._PendingMessage({'data': [raw]})
                await client._dispatch_handlers(
                    raw['service'], raw, dispatch_state=pending,
                    label_message=True)
                self.assertEqual(original, raw)
                self.assertEqual(handler_count, len(received))
                for message in received:
                    self.assertEqual([1, 2],
                                     message['content'][0]['nested']['values'])
                    self.assertEqual(123, message['content'][0]['BID_PRICE'])
