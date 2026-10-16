from schwab import auth
from .utils import (
        AnyStringWith,
        MockAsyncOAuthClient,
        MockOAuthClient,
        no_duplicates
)
from unittest.mock import patch, ANY, MagicMock
from unittest.mock import ANY as _

import json
import os
import requests
import tempfile
import unittest


API_KEY = 'APIKEY'
APP_SECRET = '0x5EC07'
TOKEN_CREATION_TIMESTAMP = 1613745000
MOCK_NOW = 1613745082
CALLBACK_URL = 'https://redirect.url.com'


class ClientFromLoginFlowTest(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.token_path = os.path.join(self.tmp_dir.name, 'token.json')
        self.raw_token = {'token': 'yes'}
        self.token = {
                'token': self.raw_token,
                'creation_timestamp': TOKEN_CREATION_TIMESTAMP
        }

    @patch('schwab.auth.Client')
    @patch('schwab.auth.OAuth2Client', new_callable=MockOAuthClient)
    @patch('schwab.auth.AsyncOAuth2Client', new_callable=MockAsyncOAuthClient)
    @patch('schwab.auth.webbrowser.get', new_callable=MagicMock)
    @patch('schwab.auth.input', MagicMock(return_value=''))
    @patch('time.time', MagicMock(return_value=MOCK_NOW))
    def test_create_token_file(
            self, mock_webbrowser_get, async_session, sync_session, client):
        AUTH_URL = 'https://auth.url.com'

        sync_session.return_value = sync_session
        sync_session.create_authorization_url.return_value = AUTH_URL, None
        sync_session.fetch_token.return_value = self.raw_token

        callback_url = 'https://127.0.0.1:6969/callback'

        controller = MagicMock()
        mock_webbrowser_get.return_value = controller
        controller.open.side_effect = \
                lambda auth_url: requests.get(
                        'https://127.0.0.1:6969/callback', verify=False)

        client.return_value = 'returned client'

        auth.client_from_login_flow(
                API_KEY, APP_SECRET, callback_url, self.token_path)

        with open(self.token_path, 'r') as f:
            self.assertEqual({
                'creation_timestamp': MOCK_NOW,
                'token': self.raw_token
            }, json.load(f))


    @patch('schwab.auth.Client')
    @patch('schwab.auth.OAuth2Client', new_callable=MockOAuthClient)
    @patch('schwab.auth.AsyncOAuth2Client', new_callable=MockAsyncOAuthClient)
    @patch('schwab.auth.webbrowser.get', new_callable=MagicMock)
    @patch('schwab.auth.input', MagicMock(return_value=''))
    @patch('time.time', MagicMock(return_value=MOCK_NOW))
    def test_specify_web_browser(
            self, mock_webbrowser_get, async_session, sync_session, client):
        AUTH_URL = 'https://auth.url.com'

        sync_session.return_value = sync_session
        sync_session.create_authorization_url.return_value = AUTH_URL, None
        sync_session.fetch_token.return_value = self.raw_token

        callback_url = 'https://127.0.0.1:6969/callback'

        controller = MagicMock()
        mock_webbrowser_get.return_value = controller
        controller.open.side_effect = \
                lambda auth_url: requests.get(
                        'https://127.0.0.1:6969/callback', verify=False)

        auth.client_from_login_flow(
                API_KEY, APP_SECRET, callback_url, self.token_path,
                requested_browser='custom-browser')

        mock_webbrowser_get.assert_called_with('custom-browser')


    @patch('schwab.auth.Client')
    @patch('schwab.auth.OAuth2Client', new_callable=MockOAuthClient)
    @patch('schwab.auth.AsyncOAuth2Client', new_callable=MockAsyncOAuthClient)
    @patch('schwab.auth.webbrowser.get', new_callable=MagicMock)
    @patch('schwab.auth.input')
    @patch('time.time', MagicMock(return_value=MOCK_NOW))
    def test_create_token_file_not_interactive(
            self, mock_prompt,mock_webbrowser_get, async_session, sync_session,
            client):
        AUTH_URL = 'https://auth.url.com'

        sync_session.return_value = sync_session
        sync_session.create_authorization_url.return_value = AUTH_URL, None
        sync_session.fetch_token.return_value = self.raw_token

        callback_url = 'https://127.0.0.1:6969/callback'

        controller = MagicMock()
        mock_webbrowser_get.return_value = controller
        controller.open.side_effect = \
               lambda auth_url: requests.get(
                        'https://127.0.0.1:6969/callback', verify=False)

        client.return_value = 'returned client'

        auth.client_from_login_flow(
                API_KEY, APP_SECRET, callback_url, self.token_path, 
                interactive=False)

        with open(self.token_path, 'r') as f:
            self.assertEqual({
                'creation_timestamp': MOCK_NOW,
                'token': self.raw_token
            }, json.load(f))

        mock_prompt.assert_not_called()


    @patch('schwab.auth.Client')
    @patch('schwab.auth.OAuth2Client', new_callable=MockOAuthClient)
    @patch('schwab.auth.AsyncOAuth2Client', new_callable=MockAsyncOAuthClient)
    @patch('schwab.auth.webbrowser.get', new_callable=MagicMock)
    @patch('schwab.auth.input', MagicMock(return_value=''))
    @patch('time.time', MagicMock(return_value=MOCK_NOW))
    def test_create_token_file_root_callback_url(
            self, mock_webbrowser_get, async_session, sync_session, client):
        AUTH_URL = 'https://auth.url.com'

        sync_session.return_value = sync_session
        sync_session.create_authorization_url.return_value = AUTH_URL, None
        sync_session.fetch_token.return_value = self.raw_token

        callback_url = 'https://127.0.0.1:6969/'

        controller = MagicMock()
        mock_webbrowser_get.return_value = controller
        controller.open.side_effect = \
               lambda auth_url: requests.get(
                        'https://127.0.0.1:6969/', verify=False)

        client.return_value = 'returned client'

        auth.client_from_login_flow(
                API_KEY, APP_SECRET, callback_url, self.token_path)

        with open(self.token_path, 'r') as f:
            self.assertEqual({
                'creation_timestamp': MOCK_NOW,
                'token': self.raw_token
            }, json.load(f))


    @patch('schwab.auth.Client')
    @patch('schwab.auth.OAuth2Client', new_callable=MockOAuthClient)
    @patch('schwab.auth.AsyncOAuth2Client', new_callable=MockAsyncOAuthClient)
    @patch('schwab.auth.webbrowser.get', new_callable=MagicMock)
    @patch('time.time', MagicMock(return_value=MOCK_NOW))
    def test_disallowed_hostname(
            self, mock_webbrowser_get, async_session, sync_session, client):
        callback_url = 'https://example.com/callback'

        with self.assertRaisesRegex(
                ValueError, 'Disallowed hostname example.com'):
            auth.client_from_login_flow(
                    API_KEY, APP_SECRET, callback_url, self.token_path)


    @patch('schwab.auth.Client')
    @patch('schwab.auth.OAuth2Client', new_callable=MockOAuthClient)
    @patch('schwab.auth.AsyncOAuth2Client', new_callable=MockAsyncOAuthClient)
    @patch('schwab.auth.webbrowser.get', new_callable=MagicMock)
    @patch('time.time', MagicMock(return_value=MOCK_NOW))
    def test_negative_timeout(
            self, mock_webbrowser_get, async_session, sync_session, client):
        callback_url = 'https://example.com/callback'

        with self.assertRaisesRegex(
                ValueError, 'callback_timeout must be positive'):
            auth.client_from_login_flow(
                    API_KEY, APP_SECRET, callback_url, self.token_path,
                    callback_timeout=-1)


    @patch('schwab.auth.Client')
    @patch('schwab.auth.OAuth2Client', new_callable=MockOAuthClient)
    @patch('schwab.auth.AsyncOAuth2Client', new_callable=MockAsyncOAuthClient)
    @patch('schwab.auth.webbrowser.get', new_callable=MagicMock)
    @patch('time.time', MagicMock(return_value=MOCK_NOW))
    def test_disallowed_hostname_with_port(
            self, mock_webbrowser_get, async_session, sync_session, client):
        callback_url = 'https://example.com:8080/callback'

        with self.assertRaisesRegex(
                ValueError, 'Disallowed hostname example.com'):
            auth.client_from_login_flow(
                    API_KEY, APP_SECRET, callback_url, self.token_path)


    @patch('schwab.auth.Client')
    @patch('schwab.auth.OAuth2Client', new_callable=MockOAuthClient)
    @patch('schwab.auth.AsyncOAuth2Client', new_callable=MockAsyncOAuthClient)
    @patch('schwab.auth.webbrowser.get', new_callable=MagicMock)
    @patch('time.time', MagicMock(return_value=MOCK_NOW))
    def test_start_on_port_443(
            self, mock_webbrowser_get, async_session, sync_session, client):
        callback_url = 'https://127.0.0.1/callback'

        with self.assertRaisesRegex(auth.RedirectServerExitedError,
                                    'callback URL without a port number'):
            auth.client_from_login_flow(
                    API_KEY, APP_SECRET, callback_url, self.token_path)


    @patch('schwab.auth.Client')
    @patch('schwab.auth.OAuth2Client', new_callable=MockOAuthClient)
    @patch('schwab.auth.AsyncOAuth2Client', new_callable=MockAsyncOAuthClient)
    @patch('schwab.auth.webbrowser.get', new_callable=MagicMock)
    @patch('schwab.auth.input', MagicMock(return_value=''))
    @patch('time.time', MagicMock(return_value=MOCK_NOW))
    def test_time_out_waiting_for_request(
            self, mock_webbrowser_get, async_session, sync_session, client):
        AUTH_URL = 'https://auth.url.com'

        sync_session.return_value = sync_session
        sync_session.create_authorization_url.return_value = AUTH_URL, None
        sync_session.fetch_token.return_value = self.raw_token

        callback_url = 'https://127.0.0.1:6969/callback'

        with self.assertRaisesRegex(auth.RedirectTimeoutError,
                                    'Timed out waiting'):
            auth.client_from_login_flow(
                    API_KEY, APP_SECRET, callback_url, self.token_path,
                    callback_timeout=0.01)


    @patch('schwab.auth.Client')
    @patch('schwab.auth.OAuth2Client', new_callable=MockOAuthClient)
    @patch('schwab.auth.AsyncOAuth2Client', new_callable=MockAsyncOAuthClient)
    @patch('schwab.auth.webbrowser.get', new_callable=MagicMock)
    @patch('schwab.auth.input', MagicMock(return_value=''))
    @patch('time.time', MagicMock(return_value=MOCK_NOW))
    def test_wait_forever_callback_timeout_equals_none(
            self, mock_webbrowser_get, async_session, sync_session, client):
        AUTH_URL = 'https://auth.url.com'

        sync_session.return_value = sync_session
        sync_session.create_authorization_url.return_value = AUTH_URL, None
        sync_session.fetch_token.return_value = self.raw_token

        callback_url = 'https://127.0.0.1:6969/callback'

        with self.assertRaisesRegex(ValueError, 'endless wait requested'):
            auth.client_from_login_flow(
                    API_KEY, APP_SECRET, callback_url, self.token_path,
                    callback_timeout=None)


    @patch('schwab.auth.Client')
    @patch('schwab.auth.OAuth2Client', new_callable=MockOAuthClient)
    @patch('schwab.auth.AsyncOAuth2Client', new_callable=MockAsyncOAuthClient)
    @patch('schwab.auth.webbrowser.get', new_callable=MagicMock)
    @patch('schwab.auth.input', MagicMock(return_value=''))
    @patch('time.time', MagicMock(return_value=MOCK_NOW))
    def test_wait_forever_callback_timeout_equals_zero(
            self, mock_webbrowser_get, async_session, sync_session, client):
        AUTH_URL = 'https://auth.url.com'

        sync_session.return_value = sync_session
        sync_session.create_authorization_url.return_value = AUTH_URL, None
        sync_session.fetch_token.return_value = self.raw_token

        callback_url = 'https://127.0.0.1:6969/callback'

        with self.assertRaisesRegex(ValueError, 'endless wait requested'):
            auth.client_from_login_flow(
                    API_KEY, APP_SECRET, callback_url, self.token_path,
                    callback_timeout=0)


class CallbackServerBindingTest(unittest.TestCase):

    def test_macos_binds_loopback_tls_without_reverse_dns(self):
        import flask
        import ssl
        from werkzeug.serving import ThreadedWSGIServer

        servers = []

        def capture_server(server):
            servers.append(server)
            self.assertEqual(server.server_address[0], '127.0.0.1')
            self.assertGreater(server.server_address[1], 0)
            self.assertEqual(server.server_name, '127.0.0.1')
            self.assertEqual(server.server_port, server.server_address[1])
            self.assertIsInstance(server.socket, ssl.SSLSocket)
            self.assertIsNotNone(server.ssl_context)
            self.assertTrue(server.multithread)

        with patch('schwab.auth.sys', MagicMock(platform='darwin')), \
                patch('socket.getfqdn', side_effect=AssertionError(
                    'callback startup must not depend on reverse DNS')) as lookup, \
                patch.object(ThreadedWSGIServer, 'serve_forever', capture_server):
            auth._run_callback_server(flask.Flask(__name__), 0)

        lookup.assert_not_called()
        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0].socket.fileno(), -1)

    def test_other_platforms_keep_flask_server_settings(self):
        for platform in ('linux', 'win32'):
            with self.subTest(platform=platform), \
                    patch('schwab.auth.sys', MagicMock(platform=platform)):
                app = MagicMock()
                auth._run_callback_server(app, 8182)
                app.run.assert_called_once_with(port=8182, ssl_context='adhoc')


class CallbackProcessContextTest(unittest.TestCase):

    def assert_callback_context(self, platform, use_spawn):
        with patch('schwab.auth.sys', MagicMock(platform=platform)), \
                patch('schwab.auth.multiprocess') as multiprocess, \
                patch('schwab.auth.secrets.token_urlsafe',
                      return_value='synthetic-readiness'), \
                patch('schwab.auth.psutil.Process') as cleanup, \
                patch('schwab.auth._wait_for_callback_server',
                      side_effect=auth.RedirectServerExitedError(
                          'stop before authentication')) as wait_for_server:
            context = (multiprocess.get_context.return_value
                       if use_spawn else multiprocess)
            queue = context.Queue.return_value
            server = context.Process.return_value

            with self.assertRaisesRegex(auth.RedirectServerExitedError,
                                        'stop before authentication'):
                auth.client_from_login_flow(
                    API_KEY, APP_SECRET,
                    'https://127.0.0.1:8182/oauth%20callback;v1',
                    'unused', interactive=False)

            context.Queue.assert_called_once_with()
            context.Process.assert_called_once_with(
                target=getattr(auth, '__run_client_from_login_flow_server'),
                args=(queue, 8182, '/oauth callback;v1',
                      'synthetic-readiness'))
            server.start.assert_called_once_with()
            wait_for_server.assert_called_once_with(
                server, 8182, 'synthetic-readiness')
            cleanup.assert_called_once_with(server.pid)
            cleanup.return_value.kill.assert_called_once_with()
            server.join.assert_called_once_with(timeout=1.0)
            multiprocess.set_start_method.assert_not_called()

            if use_spawn:
                multiprocess.get_context.assert_called_once_with('spawn')
                multiprocess.Queue.assert_not_called()
                multiprocess.Process.assert_not_called()
            else:
                multiprocess.get_context.assert_not_called()

    def test_macos_uses_one_spawn_context_for_queue_and_process(self):
        self.assert_callback_context('darwin', use_spawn=True)

    def test_other_platforms_keep_the_default_context(self):
        for platform in ('linux', 'win32'):
            with self.subTest(platform=platform):
                self.assert_callback_context(platform, use_spawn=False)


class CallbackServerReadinessTest(unittest.TestCase):

    def setUp(self):
        self.server = MagicMock()
        self.server.exitcode = None
        self.callback_port = 6969
        self.readiness_token = 'unique-readiness-token'

    @no_duplicates
    @patch('schwab.auth.httpx.get')
    def test_accepts_expected_server_response(self, http_get):
        http_get.return_value.status_code = 200
        http_get.return_value.text = self.readiness_token

        auth._wait_for_callback_server(
                self.server, self.callback_port, self.readiness_token)

    @no_duplicates
    @patch('schwab.auth.httpx.get')
    def test_rejects_non_200_response(self, http_get):
        http_get.return_value.status_code = 503
        http_get.return_value.text = self.readiness_token

        with self.assertRaisesRegex(
                auth.RedirectServerIdentityError, 'unexpected HTTPS server'):
            auth._wait_for_callback_server(
                    self.server, self.callback_port, self.readiness_token)

    @no_duplicates
    @patch('schwab.auth.httpx.get')
    def test_rejects_wrong_readiness_token(self, http_get):
        http_get.return_value.status_code = 200
        http_get.return_value.text = 'response-from-another-server'

        with self.assertRaisesRegex(
                auth.RedirectServerIdentityError, 'unexpected HTTPS server'):
            auth._wait_for_callback_server(
                    self.server, self.callback_port, self.readiness_token)

    @no_duplicates
    @patch('schwab.auth.time.sleep')
    @patch('schwab.auth.httpx.get')
    def test_retries_connection_and_response_timeouts(
            self, http_get, sleep):
        successful_response = MagicMock()
        successful_response.status_code = 200
        successful_response.text = self.readiness_token

        for timeout_type in (
                auth.httpx.ConnectTimeout, auth.httpx.ReadTimeout):
            with self.subTest(timeout_type=timeout_type):
                http_get.side_effect = [
                    timeout_type('server not ready'), successful_response]

                auth._wait_for_callback_server(
                        self.server, self.callback_port,
                        self.readiness_token)

                self.assertEqual(http_get.call_count, 2)
                sleep.assert_called_once_with(
                        auth._CALLBACK_SERVER_POLL_INTERVAL)
                http_get.reset_mock()
                sleep.reset_mock()

    @no_duplicates
    @patch('schwab.auth.time.monotonic', side_effect=[0.0, 31.0])
    def test_startup_wait_has_deadline(self, monotonic):
        with self.assertRaisesRegex(
                auth.RedirectServerTimeoutError,
                'Timed out waiting for the local OAuth callback server'):
            auth._wait_for_callback_server(
                    self.server, self.callback_port, self.readiness_token)


class ClientFromTokenFileTest(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.token_path = os.path.join(self.tmp_dir.name, 'token.json')
        self.raw_token = {'token': 'yes'}
        self.token = {
                'token': self.raw_token,
                'creation_timestamp': TOKEN_CREATION_TIMESTAMP
        }

    def write_token(self):
        with open(self.token_path, 'w') as f:
            json.dump(self.token, f)

    @no_duplicates
    def test_no_such_file(self):
        with self.assertRaises(FileNotFoundError):
            auth.client_from_token_file(self.token_path, API_KEY, APP_SECRET)

    @no_duplicates
    @patch('schwab.auth.Client')
    @patch('schwab.auth.OAuth2Client', new_callable=MockOAuthClient)
    @patch('schwab.auth.AsyncOAuth2Client', new_callable=MockAsyncOAuthClient)
    def test_json_loads(self, async_session, sync_session, client):
        self.write_token()

        client.return_value = 'returned client'

        self.assertEqual('returned client',
                         auth.client_from_token_file(
                             self.token_path, API_KEY, APP_SECRET))
        client.assert_called_once_with(API_KEY, _, token_metadata=_,
                                       enforce_enums=_)
        sync_session.assert_called_once_with(
            API_KEY,
            client_secret=APP_SECRET,
            token=self.raw_token,
            token_endpoint=_,
            update_token=_,
            leeway=_)

    @no_duplicates
    @patch('schwab.auth.Client')
    @patch('schwab.auth.OAuth2Client', new_callable=MockOAuthClient)
    @patch('schwab.auth.AsyncOAuth2Client', new_callable=MockAsyncOAuthClient)
    def test_update_token_updates_token(
            self, async_session, sync_session, client):
        self.write_token()

        auth.client_from_token_file(self.token_path, API_KEY, APP_SECRET)
        sync_session.assert_called_once()

        session_call = sync_session.mock_calls[0]
        update_token = session_call[2]['update_token']

        updated_token = {'updated': 'token'}
        update_token(updated_token)
        with open(self.token_path, 'r') as f:
            self.assertEqual(json.load(f), {
                'token': updated_token,
                'creation_timestamp': TOKEN_CREATION_TIMESTAMP
            })

        if os.name == 'posix':
            self.assertEqual(os.stat(self.token_path).st_mode & 0o777, 0o600)

    @no_duplicates
    @unittest.skipUnless(os.name == 'posix', 'requires POSIX symlinks')
    @patch('schwab.auth.Client')
    @patch('schwab.auth.OAuth2Client', new_callable=MockOAuthClient)
    @patch('schwab.auth.AsyncOAuth2Client', new_callable=MockAsyncOAuthClient)
    def test_update_token_preserves_symlink(
            self, async_session, sync_session, client):
        target_path = os.path.join(self.tmp_dir.name, 'shared-token.json')
        with open(target_path, 'w') as f:
            json.dump(self.token, f)
        os.symlink(target_path, self.token_path)

        auth.client_from_token_file(
                self.token_path, API_KEY, APP_SECRET)
        update_token = sync_session.mock_calls[0][2]['update_token']

        updated_token = {'updated': 'token'}
        update_token(updated_token)

        self.assertTrue(os.path.islink(self.token_path))
        with open(target_path, 'r') as f:
            self.assertEqual(json.load(f), {
                'token': updated_token,
                'creation_timestamp': TOKEN_CREATION_TIMESTAMP
            })

    @no_duplicates
    @patch('schwab.auth.Client')
    @patch('schwab.auth.OAuth2Client', new_callable=MockOAuthClient)
    @patch('schwab.auth.AsyncOAuth2Client', new_callable=MockAsyncOAuthClient)
    def test_failed_token_update_preserves_existing_file(
            self, async_session, sync_session, client):
        self.write_token()

        auth.client_from_token_file(self.token_path, API_KEY, APP_SECRET)
        update_token = sync_session.mock_calls[0][2]['update_token']

        with self.assertRaises(TypeError):
            update_token({'not-json-serializable': object()})

        with open(self.token_path, 'r') as f:
            self.assertEqual(json.load(f), self.token)

    @no_duplicates
    @patch('schwab.auth.Client')
    @patch('schwab.auth.OAuth2Client', new_callable=MockOAuthClient)
    @patch('schwab.auth.AsyncOAuth2Client', new_callable=MockAsyncOAuthClient)
    def test_enforce_enums_being_disabled(self, async_session, sync_session, client):
        self.write_token()

        client.return_value = 'returned client'

        self.assertEqual('returned client',
                         auth.client_from_token_file(
                             self.token_path, API_KEY, APP_SECRET,
                             enforce_enums=False))
        client.assert_called_once_with(API_KEY, _, token_metadata=_,
                                       enforce_enums=False)

    @no_duplicates
    @patch('schwab.auth.Client')
    @patch('schwab.auth.OAuth2Client', new_callable=MockOAuthClient)
    @patch('schwab.auth.AsyncOAuth2Client', new_callable=MockAsyncOAuthClient)
    def test_enforce_enums_being_enabled(self, async_session, sync_session, client):
        self.write_token()

        client.return_value = 'returned client'

        self.assertEqual('returned client',
                         auth.client_from_token_file(
                             self.token_path, API_KEY, APP_SECRET))
        client.assert_called_once_with(API_KEY, _, token_metadata=_,
                                       enforce_enums=True)


class ClientFromAccessFunctionsTest(unittest.TestCase):


    def setUp(self):
        self.raw_token = {'token': 'yes'}
        self.token = {
                'token': self.raw_token,
                'creation_timestamp': TOKEN_CREATION_TIMESTAMP
        }


    @no_duplicates
    @patch('schwab.auth.Client')
    @patch('schwab.auth.OAuth2Client', new_callable=MockOAuthClient)
    @patch('schwab.auth.AsyncOAuth2Client', new_callable=MockAsyncOAuthClient)
    def test_success_with_write_func(
            self, async_session, sync_session, client):
        token_read_func = MagicMock()
        token_read_func.return_value = self.token

        token_writes = []

        def token_write_func(token):
            token_writes.append(token)

        client.return_value = 'returned client'
        self.assertEqual('returned client',
                         auth.client_from_access_functions(
                             API_KEY,
                             APP_SECRET,
                             token_read_func,
                             token_write_func))

        sync_session.assert_called_once_with(
            API_KEY,
            client_secret=APP_SECRET,
            token=self.raw_token,
            token_endpoint=_,
            update_token=_,
            leeway=_)
        token_read_func.assert_called_once()

        # Verify that the write function is called when the updater is called
        session_call = sync_session.mock_calls[0]
        update_token = session_call[2]['update_token']

        update_token(self.raw_token)
        self.assertEqual([{
            'creation_timestamp': TOKEN_CREATION_TIMESTAMP,
            'token': self.raw_token
        }], token_writes)

    @no_duplicates
    @patch('schwab.auth.Client')
    @patch('schwab.auth.OAuth2Client', new_callable=MockOAuthClient)
    @patch('schwab.auth.AsyncOAuth2Client', new_callable=MockAsyncOAuthClient)
    def test_success_with_write_func_metadata_aware_token(
            self, async_session, sync_session, client):
        token_read_func = MagicMock()
        token_read_func.return_value = self.token

        token_writes = []

        def token_write_func(token):
            token_writes.append(token)

        client.return_value = 'returned client'
        self.assertEqual('returned client',
                         auth.client_from_access_functions(
                             API_KEY,
                             APP_SECRET,
                             token_read_func,
                             token_write_func))

        sync_session.assert_called_once_with(
            API_KEY,
            client_secret=APP_SECRET,
            token=self.raw_token,
            token_endpoint=_,
            update_token=_,
            leeway=_)
        token_read_func.assert_called_once()

        # Verify that the write function is called when the updater is called
        session_call = sync_session.mock_calls[0]
        update_token = session_call[2]['update_token']

        update_token(self.raw_token)
        self.assertEqual([{
            'creation_timestamp': TOKEN_CREATION_TIMESTAMP,
            'token': self.raw_token
        }], token_writes)

    @no_duplicates
    @patch('schwab.auth.Client')
    @patch('schwab.auth.OAuth2Client', new_callable=MockOAuthClient)
    @patch('schwab.auth.AsyncOAuth2Client', new_callable=MockAsyncOAuthClient)
    def test_success_with_enforce_enums_disabled(
            self, async_session, sync_session, client):
        token_read_func = MagicMock()
        token_read_func.return_value = self.token

        token_writes = []

        def token_write_func(token):
            token_writes.append(token)

        client.return_value = 'returned client'
        self.assertEqual('returned client',
                         auth.client_from_access_functions(
                             API_KEY,
                             APP_SECRET,
                             token_read_func,
                             token_write_func, enforce_enums=False))

        client.assert_called_once_with(
                API_KEY, _, token_metadata=_, enforce_enums=False)

    @no_duplicates
    @patch('schwab.auth.Client')
    @patch('schwab.auth.OAuth2Client', new_callable=MockOAuthClient)
    @patch('schwab.auth.AsyncOAuth2Client', new_callable=MockAsyncOAuthClient)
    def test_success_with_enforce_enums_enabled(
            self, async_session, sync_session, client):
        token_read_func = MagicMock()
        token_read_func.return_value = self.token

        token_writes = []

        def token_write_func(token):
            token_writes.append(token)

        client.return_value = 'returned client'
        self.assertEqual('returned client',
                         auth.client_from_access_functions(
                             API_KEY,
                             APP_SECRET,
                             token_read_func,
                             token_write_func))

        client.assert_called_once_with(
                API_KEY, _, token_metadata=_, enforce_enums=True)


# Note the client_from_received_url is called internally by the other client 
# generation functions, so testing here is kept light
class ClientFromReceivedUrl(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.token_path = os.path.join(self.tmp_dir.name, 'token.json')
        self.raw_token = {'token': 'yes'}

    @no_duplicates
    @patch('schwab.auth.Client')
    @patch('schwab.auth.AsyncClient')
    @patch('schwab.auth.OAuth2Client', new_callable=MockOAuthClient)
    @patch('schwab.auth.AsyncOAuth2Client', new_callable=MockAsyncOAuthClient)
    @patch('time.time', MagicMock(return_value=MOCK_NOW))
    def test_success_sync(
            self, async_session, sync_session, async_client, client):
        AUTH_URL = 'https://auth.url.com'

        sync_session.return_value = sync_session
        sync_session.create_authorization_url.return_value = \
                AUTH_URL, 'oauth state'
        sync_session.fetch_token.return_value = self.raw_token

        auth_context = auth.get_auth_context(API_KEY, CALLBACK_URL)
        self.assertEqual(AUTH_URL, auth_context.authorization_url)
        self.assertEqual('oauth state', auth_context.state)

        client.return_value = 'returned client'
        token_capture = []
        auth.client_from_received_url(
                API_KEY, APP_SECRET, auth_context, 
                'http://redirect.url.com/?data',
                lambda token: token_capture.append(token))

        client.assert_called_once()
        async_client.assert_not_called()

        # Verify that the oauth state is correctly passed along
        sync_session.fetch_token.assert_called_once_with(
                _,
                authorization_response=_,
                client_id=_,
                auth=_,
                state='oauth state')

        self.assertEqual([{
                'creation_timestamp': MOCK_NOW,
                'token': self.raw_token
            }], token_capture)


    @no_duplicates
    @patch('schwab.auth.Client')
    @patch('schwab.auth.AsyncClient')
    @patch('schwab.auth.OAuth2Client', new_callable=MockOAuthClient)
    @patch('schwab.auth.AsyncOAuth2Client', new_callable=MockAsyncOAuthClient)
    @patch('time.time', MagicMock(return_value=MOCK_NOW))
    def test_success_async(
            self, async_session, sync_session, async_client, client):
        AUTH_URL = 'https://auth.url.com'

        sync_session.return_value = sync_session
        sync_session.create_authorization_url.return_value = \
                AUTH_URL, 'oauth state'
        sync_session.fetch_token.return_value = self.raw_token

        auth_context = auth.get_auth_context(API_KEY, CALLBACK_URL)

        client.return_value = 'returned client'
        token_capture = []
        auth.client_from_received_url(
                API_KEY, APP_SECRET, auth_context, 
                'http://redirect.url.com/?data',
                lambda token: token_capture.append(token),
                asyncio=True)

        async_client.assert_called_once()
        client.assert_not_called()

        # Verify that the oauth state is correctly passed along
        sync_session.fetch_token.assert_called_once_with(
                _,
                authorization_response=_,
                client_id=_,
                auth=_,
                state='oauth state')

        self.assertEqual([{
                'creation_timestamp': MOCK_NOW,
                'token': self.raw_token
            }], token_capture)


class ClientFromManualFlow(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.token_path = os.path.join(self.tmp_dir.name, 'token.json')
        self.raw_token = {'token': 'yes'}

    @no_duplicates
    @patch('schwab.auth.Client')
    @patch('schwab.auth.OAuth2Client', new_callable=MockOAuthClient)
    @patch('schwab.auth.AsyncOAuth2Client', new_callable=MockAsyncOAuthClient)
    @patch('schwab.auth.input')
    @patch('time.time', MagicMock(return_value=MOCK_NOW))
    def test_no_token_file(
            self, prompt_func, async_session, sync_session, client):
        AUTH_URL = 'https://auth.url.com'

        sync_session.return_value = sync_session
        sync_session.create_authorization_url.return_value = AUTH_URL, None
        sync_session.fetch_token.return_value = self.raw_token

        client.return_value = 'returned client'
        prompt_func.return_value = 'http://redirect.url.com/?data'

        self.assertEqual('returned client',
                         auth.client_from_manual_flow(
                             API_KEY, APP_SECRET, CALLBACK_URL, self.token_path))

        with open(self.token_path, 'r') as f:
            self.assertEqual({
                'creation_timestamp': MOCK_NOW,
                'token': self.raw_token
            }, json.load(f))

    @no_duplicates
    @patch('schwab.auth.Client')
    @patch('schwab.auth.OAuth2Client', new_callable=MockOAuthClient)
    @patch('schwab.auth.AsyncOAuth2Client', new_callable=MockAsyncOAuthClient)
    @patch('schwab.auth.input')
    @patch('time.time', MagicMock(return_value=MOCK_NOW))
    def test_custom_token_write_func(
            self, prompt_func, async_session, sync_session, client):
        AUTH_URL = 'https://auth.url.com'

        sync_session.return_value = sync_session
        sync_session.create_authorization_url.return_value = AUTH_URL, None
        sync_session.fetch_token.return_value = self.raw_token

        client.return_value = 'returned client'
        prompt_func.return_value = 'http://redirect.url.com/?data'

        token_writes = []

        def dummy_token_write_func(token):
            token_writes.append(token)

        self.assertEqual('returned client',
                         auth.client_from_manual_flow(
                             API_KEY, APP_SECRET, CALLBACK_URL,
                             self.token_path,
                             token_write_func=dummy_token_write_func))

        sync_session.assert_called_with(
                _, client_secret=APP_SECRET, token=_,
                token_endpoint=auth.TOKEN_ENDPOINT, update_token=_, leeway=_)

        self.assertEqual([{
            'creation_timestamp': MOCK_NOW,
            'token': self.raw_token
        }], token_writes)

    @no_duplicates
    @patch('schwab.auth.Client')
    @patch('schwab.auth.OAuth2Client', new_callable=MockOAuthClient)
    @patch('schwab.auth.AsyncOAuth2Client', new_callable=MockAsyncOAuthClient)
    @patch('schwab.auth.input')
    @patch('builtins.print')
    @patch('time.time', MagicMock(return_value=MOCK_NOW))
    def test_print_warning_on_http_redirect_uri(
            self, print_func, prompt_func, async_session, sync_session, client):
        auth_url = 'https://auth.url.com'

        redirect_url = 'http://redirect.url.com'

        sync_session.return_value = sync_session
        sync_session.create_authorization_url.return_value = auth_url, None
        sync_session.fetch_token.return_value = self.raw_token

        client.return_value = 'returned client'
        prompt_func.return_value = 'http://redirect.url.com/?data'

        self.assertEqual('returned client',
                         auth.client_from_manual_flow(
                             API_KEY, APP_SECRET, redirect_url, self.token_path))

        with open(self.token_path, 'r') as f:
            self.assertEqual({
                'creation_timestamp': MOCK_NOW,
                'token': self.raw_token
            }, json.load(f))

        print_func.assert_any_call(AnyStringWith('will transmit data over HTTP'))

    @no_duplicates
    @patch('schwab.auth.Client')
    @patch('schwab.auth.OAuth2Client', new_callable=MockOAuthClient)
    @patch('schwab.auth.AsyncOAuth2Client', new_callable=MockAsyncOAuthClient)
    @patch('schwab.auth.input')
    @patch('time.time', MagicMock(return_value=MOCK_NOW))
    def test_enforce_enums_disabled(
            self, prompt_func, async_session, sync_session, client):
        auth_url = 'https://auth.url.com'

        sync_session.return_value = sync_session
        sync_session.create_authorization_url.return_value = auth_url, None
        sync_session.fetch_token.return_value = self.raw_token

        client.return_value = 'returned client'
        prompt_func.return_value = 'http://redirect.url.com/?data'

        self.assertEqual('returned client',
                         auth.client_from_manual_flow(
                             API_KEY, APP_SECRET, CALLBACK_URL, self.token_path,
                             enforce_enums=False))

        client.assert_called_once_with(API_KEY, _, token_metadata=_,
                                       enforce_enums=False)

    @no_duplicates
    @patch('schwab.auth.Client')
    @patch('schwab.auth.OAuth2Client', new_callable=MockOAuthClient)
    @patch('schwab.auth.AsyncOAuth2Client', new_callable=MockAsyncOAuthClient)
    @patch('schwab.auth.input')
    @patch('time.time', MagicMock(return_value=MOCK_NOW))
    def test_enforce_enums_enabled(
            self, prompt_func, async_session, sync_session, client):
        auth_url = 'https://auth.url.com'

        sync_session.return_value = sync_session
        sync_session.create_authorization_url.return_value = auth_url, None
        sync_session.fetch_token.return_value = self.raw_token

        client.return_value = 'returned client'
        prompt_func.return_value = 'http://redirect.url.com/?data'

        self.assertEqual('returned client',
                         auth.client_from_manual_flow(
                             API_KEY, APP_SECRET, CALLBACK_URL, self.token_path))

        client.assert_called_once_with(API_KEY, _, token_metadata=_,
                                       enforce_enums=True)


class TokenMetadataTest(unittest.TestCase):

    def test_failed_persistence_keeps_refreshed_token_in_memory(self):
        old_token = {'access_token': 'old', 'token_type': 'Bearer'}
        writer = MagicMock(side_effect=PermissionError('token file locked'))
        metadata = auth.TokenMetadata(old_token, 123, writer)
        session = auth.OAuth2Client(
                'client-id', token=old_token,
                update_token=metadata.wrapped_token_write_func())
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            'access_token': 'new', 'token_type': 'Bearer', 'expires_in': 1800}
        try:
            with patch.object(session, '_http_post', return_value=response):
                with self.assertRaises(PermissionError):
                    session._refresh_token('https://example.invalid/token')
            self.assertEqual('new', session.token['access_token'])
            self.assertIs(metadata.token, session.token)
            self.assertEqual(123, metadata.creation_timestamp)
            writer.assert_called_once()
        finally:
            session.close()

    @no_duplicates
    def test_from_loaded_token(self):
        token = {'token': 'yes', 'creation_timestamp': TOKEN_CREATION_TIMESTAMP}

        metadata = auth.TokenMetadata.from_loaded_token(
                token, unwrapped_token_write_func=None)
        self.assertEqual(metadata.token, token['token'])


    @no_duplicates
    def test_wrapped_token_write_func_updates_stored_token(self):
        token = {'token': 'yes', 'creation_timestamp': TOKEN_CREATION_TIMESTAMP}

        updated = [False]
        def update_token(token):
            updated[0] = True

        metadata = auth.TokenMetadata.from_loaded_token(
                token, unwrapped_token_write_func=update_token)

        new_token = {'updated': 'yes'}
        metadata.wrapped_token_write_func()(new_token)

        self.assertTrue(updated[0])
        self.assertEqual(new_token, metadata.token)


    @no_duplicates
    def test_reject_tokens_without_creation_timestamp(self):
        with self.assertRaisesRegex(ValueError, 'token format has changed'):
            metadata = auth.TokenMetadata.from_loaded_token(
                    {'token': 'yes'}, lambda t: None)


    @no_duplicates
    @patch('time.time', MagicMock(return_value=MOCK_NOW))
    def test_token_age(self):
        token = {'token': 'yes', 'creation_timestamp': TOKEN_CREATION_TIMESTAMP}

        metadata = auth.TokenMetadata.from_loaded_token(
                token, unwrapped_token_write_func=None)
        self.assertEqual(metadata.token_age(),
                         MOCK_NOW - TOKEN_CREATION_TIMESTAMP)


class EasyClientTest(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.token_path = os.path.join(self.tmp_dir.name, 'token.json')
        self.raw_token = {'token': 'yes'}

    def put_token(self):
        with open(self.token_path, 'w') as f:
            f.write(json.dumps(self.raw_token))


    @no_duplicates
    @patch('schwab.auth.client_from_token_file')
    @patch('schwab.auth.client_from_login_flow', new_callable=MockOAuthClient)
    @patch('time.time', MagicMock(return_value=MOCK_NOW))
    def test_no_token(
            self, client_from_login_flow, client_from_token_file):
        mock_client = MagicMock()
        client_from_login_flow.return_value = mock_client

        c = auth.easy_client(API_KEY, APP_SECRET, CALLBACK_URL, self.token_path)

        self.assertIs(c, mock_client)


    @no_duplicates
    @patch('schwab.auth.client_from_token_file')
    @patch('schwab.auth.client_from_login_flow', new_callable=MockOAuthClient)
    @patch('schwab.auth.client_from_manual_flow', new_callable=MockOAuthClient)
    @patch('os.getenv', new_callable=MockOAuthClient)
    @patch('time.time', MagicMock(return_value=MOCK_NOW))
    def test_running_on_collab_environment(
            self, getenv, client_from_manual_flow, client_from_login_flow, 
            client_from_token_file):
        def do_getenv(flag):
            assert flag == 'COLAB_RELEASE_TAG'
            return 'yes'
        getenv.side_effect = do_getenv

        mock_client = MagicMock()
        client_from_manual_flow.return_value = mock_client

        c = auth.easy_client(API_KEY, APP_SECRET, CALLBACK_URL, self.token_path)
        self.assertIs(c, mock_client)


    @no_duplicates
    @patch('schwab.auth.client_from_token_file')
    @patch('schwab.auth.client_from_login_flow', new_callable=MockOAuthClient)
    @patch('schwab.auth.client_from_manual_flow', new_callable=MockOAuthClient)
    @patch('os.getenv', new_callable=MockOAuthClient)
    @patch('schwab.auth._get_ipython')
    @patch('time.time', MagicMock(return_value=MOCK_NOW))
    def test_running_on_ipython_in_notebook_mode(
            self, get_ipython, getenv, client_from_manual_flow, 
            client_from_login_flow, client_from_token_file):
        getenv.return_value = ''

        class ZMQInteractiveShell:
            pass
        get_ipython.return_value = ZMQInteractiveShell()

        mock_client = MagicMock()
        client_from_manual_flow.return_value = mock_client

        c = auth.easy_client(
                API_KEY, APP_SECRET, CALLBACK_URL, self.token_path,
                asyncio=True, enforce_enums=False)
        self.assertIs(c, mock_client)
        client_from_manual_flow.assert_called_once_with(
                API_KEY, APP_SECRET, CALLBACK_URL, self.token_path,
                asyncio=True, enforce_enums=False)


    @no_duplicates
    @patch('schwab.auth.client_from_token_file')
    @patch('schwab.auth.client_from_login_flow', new_callable=MockOAuthClient)
    @patch('schwab.auth.client_from_manual_flow', new_callable=MockOAuthClient)
    @patch('os.getenv', new_callable=MockOAuthClient)
    @patch('schwab.auth._get_ipython')
    @patch('time.time', MagicMock(return_value=MOCK_NOW))
    def test_running_on_ipython_in_something_other_than_notebook_mode(
            self, get_ipython, getenv, client_from_manual_flow, 
            client_from_login_flow, client_from_token_file):
        getenv.return_value = ''

        class NotZMQInteractiveShell:
            pass
        get_ipython.return_value = NotZMQInteractiveShell()

        mock_client = MagicMock()
        client_from_login_flow.return_value = mock_client

        c = auth.easy_client(API_KEY, APP_SECRET, CALLBACK_URL, self.token_path)
        self.assertIs(c, mock_client)


    @no_duplicates
    @patch('schwab.auth.client_from_token_file')
    @patch('schwab.auth.client_from_login_flow', new_callable=MockOAuthClient)
    @patch('time.time', MagicMock(return_value=MOCK_NOW))
    def test_no_token_passing_parameters(
            self, client_from_login_flow, client_from_token_file):
        mock_client = MagicMock()
        client_from_login_flow.return_value = mock_client

        c = auth.easy_client(
                API_KEY, APP_SECRET, CALLBACK_URL, self.token_path, 
                asyncio='asyncio', enforce_enums='enforce_enums', 
                callback_timeout='callback_timeout', interactive='interactive',
                requested_browser='requested_browser')

        self.assertIs(c, mock_client)

        client_from_login_flow.assert_called_once_with(
                API_KEY, APP_SECRET, CALLBACK_URL, self.token_path,
                asyncio='asyncio', enforce_enums='enforce_enums',
                callback_timeout='callback_timeout', interactive='interactive',
                requested_browser='requested_browser')


    @no_duplicates
    @patch('schwab.auth.client_from_token_file')
    @patch('schwab.auth.client_from_login_flow', new_callable=MockOAuthClient)
    @patch('time.time', MagicMock(return_value=MOCK_NOW))
    def test_existing_token(
            self, client_from_login_flow, client_from_token_file):
        self.put_token()

        mock_client = MagicMock()
        client_from_token_file.return_value = mock_client
        mock_client.token_age.return_value = 1

        c = auth.easy_client(API_KEY, APP_SECRET, CALLBACK_URL, self.token_path)

        self.assertIs(c, mock_client)


    @no_duplicates
    @patch('schwab.auth.client_from_token_file')
    @patch('schwab.auth.client_from_login_flow', new_callable=MockOAuthClient)
    @patch('time.time', MagicMock(return_value=MOCK_NOW))
    def test_existing_token_passing_parameters(
            self, client_from_login_flow, client_from_token_file):
        self.put_token()

        mock_client = MagicMock()
        client_from_token_file.return_value = mock_client
        mock_client.token_age.return_value = 1

        c = auth.easy_client(API_KEY, APP_SECRET, CALLBACK_URL, self.token_path,
                             asyncio='asyncio', enforce_enums='enforce_enums')

        self.assertIs(c, mock_client)

        client_from_token_file.assert_called_once_with(
                self.token_path, API_KEY, APP_SECRET,
                asyncio='asyncio', enforce_enums='enforce_enums')


    @no_duplicates
    @patch('schwab.auth.client_from_token_file')
    @patch('schwab.auth.client_from_login_flow', new_callable=MockOAuthClient)
    @patch('time.time', MagicMock(return_value=MOCK_NOW))
    def test_token_too_old(
            self, client_from_login_flow, client_from_token_file):
        self.put_token()

        mock_file_client = MagicMock()
        client_from_token_file.return_value = mock_file_client
        mock_file_client.token_age.return_value = 9999999999

        mock_browser_client = MagicMock()
        client_from_login_flow.return_value = mock_browser_client
        mock_browser_client.token_age.return_value = 1

        c = auth.easy_client(API_KEY, APP_SECRET, CALLBACK_URL, self.token_path)

        self.assertIs(c, mock_browser_client)


    @no_duplicates
    @patch('schwab.auth.client_from_token_file')
    @patch('schwab.auth.client_from_login_flow', new_callable=MockOAuthClient)
    @patch('time.time', MagicMock(return_value=MOCK_NOW))
    def test_negative_max_token_age(
            self, client_from_login_flow, client_from_token_file):
        with self.assertRaisesRegex(
                ValueError, 'max_token_age must be positive, zero, or None'):
            c = auth.easy_client(API_KEY, APP_SECRET, CALLBACK_URL, 
                                 self.token_path, max_token_age=-1)


    @no_duplicates
    @patch('schwab.auth.client_from_token_file')
    @patch('schwab.auth.client_from_login_flow', new_callable=MockOAuthClient)
    @patch('time.time', MagicMock(return_value=MOCK_NOW))
    def test_none_max_token_age(
            self, client_from_login_flow, client_from_token_file):
        self.put_token()

        mock_client = MagicMock()
        client_from_token_file.return_value = mock_client
        mock_client.token_age.return_value = 9999999999

        c = auth.easy_client(API_KEY, APP_SECRET, CALLBACK_URL, self.token_path,
                             max_token_age=None)

        self.assertIs(c, mock_client)


    @no_duplicates
    @patch('schwab.auth.client_from_token_file')
    @patch('schwab.auth.client_from_login_flow', new_callable=MockOAuthClient)
    @patch('time.time', MagicMock(return_value=MOCK_NOW))
    def test_zero_max_token_age(
            self, client_from_login_flow, client_from_token_file):
        self.put_token()

        mock_client = MagicMock()
        client_from_token_file.return_value = mock_client
        mock_client.token_age.return_value = 9999999999

        c = auth.easy_client(API_KEY, APP_SECRET, CALLBACK_URL, self.token_path,
                             max_token_age=0)
