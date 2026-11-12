import unittest
from unittest.mock import Mock, patch

from schwab import auth


class ConnectionPoolTests(unittest.TestCase):
    def test_auth_clients_use_bounded_longer_keepalive(self):
        metadata = auth.TokenMetadata({'access_token': 'synthetic'}, 0, Mock())
        for asynchronous, target in [(False, 'OAuth2Client'), (True, 'AsyncOAuth2Client')]:
            with self.subTest(asynchronous=asynchronous), \
                    patch('schwab.auth.' + target) as session_class, \
                    patch('schwab.auth.Client'), patch('schwab.auth.AsyncClient'):
                auth._client_from_token_metadata('key', 'secret', metadata, asynchronous, True)
                limits = session_class.call_args.kwargs['limits']
                self.assertEqual(limits.keepalive_expiry, 60.0)
                self.assertEqual(limits.max_connections, 100)
                self.assertEqual(limits.max_keepalive_connections, 20)

class BackgroundRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_refresh_runs_before_order_and_stops_with_session(self):
        import asyncio
        import time
        from unittest.mock import AsyncMock
        from schwab.client import AsyncClient
        session = Mock()
        session.token = {'expires_at': time.time() + 299}
        session.leeway = 300
        refreshed = asyncio.Event()
        async def refresh(token):
            token['expires_at'] = time.time() + 3600
            refreshed.set()
        session.ensure_active_token = AsyncMock(side_effect=refresh)
        session.aclose = AsyncMock()
        client = AsyncClient('key', session)
        client.start_token_refresh()
        client.start_token_refresh()
        await asyncio.wait_for(refreshed.wait(), 1)
        await client.close_async_session()
        session.ensure_active_token.assert_awaited_once()
        session.aclose.assert_awaited_once()
        self.assertTrue(client._refresh_task.done())

    async def test_sync_refresh_worker_stops_before_session_close(self):
        import asyncio
        import threading
        import time
        from schwab.client import Client
        session = Mock()
        session.token = {'expires_at': time.time() + 299}
        session.leeway = 300
        refreshed = threading.Event()
        def refresh(token):
            token['expires_at'] = time.time() + 3600
            refreshed.set()
        session.ensure_active_token.side_effect = refresh
        client = Client('key', session)
        client.start_token_refresh()
        client.start_token_refresh()
        self.assertTrue(await asyncio.to_thread(refreshed.wait, 1))
        await asyncio.to_thread(client.close_session)
        session.ensure_active_token.assert_called_once()
        session.close.assert_called_once()
        self.assertFalse(client._refresh_thread.is_alive())

class RefreshFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_and_async_refresh_failures_use_only_eligible_tokens(self):
        import time
        import httpx2
        from authlib.integrations.httpx_client import OAuth2Client, AsyncOAuth2Client
        from authlib.integrations.base_client.errors import OAuthError
        from schwab.client import Client, AsyncClient

        for asynchronous in (False, True):
            with self.subTest(asynchronous=asynchronous):
                status = [503]
                calls = []
                def handle(request):
                    calls.append(str(request.url))
                    if str(request.url) == auth.TOKEN_ENDPOINT:
                        return httpx2.Response(status[0], json={'error': 'invalid_grant'})
                    return httpx2.Response(201)
                session_class = AsyncOAuth2Client if asynchronous else OAuth2Client
                session = session_class('key', client_secret='secret',
                    token={'access_token': 'old', 'refresh_token': 'refresh',
                           'token_type': 'Bearer', 'expires_at': int(time.time()) + 200},
                    token_endpoint=auth.TOKEN_ENDPOINT, leeway=300,
                    transport=httpx2.MockTransport(handle))
                if not asynchronous:
                    auth._serialize_sync_token_refresh(session)
                auth._install_refresh_fallback(session, asynchronous=asynchronous)
                client = (AsyncClient if asynchronous else Client)('key', session)
                async def place():
                    result = client.place_order('account', {})
                    return await result if asynchronous else result
                try:
                    self.assertEqual((await place()).status_code, 201)
                    self.assertEqual(len(calls), 2)
                    status[0] = 400
                    with self.assertRaises(OAuthError):
                        await place()
                    self.assertEqual(len(calls), 3)
                    client.configure_refresh_fallback(allow_oauth_errors=True)
                    self.assertEqual((await place()).status_code, 201)
                    session.token['expires_at'] = int(time.time()) + 10
                    with self.assertRaises(OAuthError):
                        await place()
                finally:
                    if asynchronous:
                        await client.close_async_session()
                    else:
                        client.close_session()
