import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx2

from schwab.client.synchronous import Client
from schwab.client.asynchronous import AsyncClient


class GetRetryTest(unittest.IsolatedAsyncioTestCase):
    async def test_transport_failures_are_retried_in_both_clients(self):
        sync_session = Mock()
        sync_session.get.side_effect = [httpx2.ConnectError('offline'), httpx2.Response(200)]
        sync = Client('key', sync_session)
        sync.configure_get_retries(1)
        with patch('schwab.client.base.random.uniform', return_value=0.25), \
                patch('schwab.client.synchronous.time.sleep') as sleep:
            self.assertEqual(200, sync._get_request('/test', {}).status_code)
        sleep.assert_called_once_with(0.25)

        async_session = Mock()
        async_session.get = AsyncMock(side_effect=[
            httpx2.ReadTimeout('slow'), httpx2.Response(200)])
        async_client = AsyncClient('key', async_session)
        async_client.configure_get_retries(1)
        with patch('schwab.client.base.random.uniform', return_value=0.1), \
                patch('schwab.client.asynchronous.asyncio.sleep', new_callable=AsyncMock) as sleep:
            self.assertEqual(200, (await async_client._get_request('/test', {})).status_code)
        sleep.assert_awaited_once_with(0.1)

    async def test_exhausted_transport_error_propagates(self):
        session = Mock()
        session.get.side_effect = httpx2.ConnectError('offline')
        client = Client('key', session)
        client.configure_get_retries(1, backoff=0)
        with patch('schwab.client.synchronous.time.sleep'):
            with self.assertRaises(httpx2.ConnectError):
                client._get_request('/test', {})
        self.assertEqual(2, session.get.call_count)

    async def test_does_not_replay_an_oauth_refresh_post(self):
        session = Mock()
        request = httpx2.Request('POST', 'https://api.schwabapi.com/v1/oauth/token')
        session.get.side_effect = httpx2.ReadTimeout('refresh timed out', request=request)
        client = Client('key', session)
        client.configure_get_retries(2)
        with self.assertRaises(httpx2.ReadTimeout):
            client._get_request('/test', {})
        self.assertEqual(1, session.get.call_count)

    async def test_async_retry_obeys_retry_after(self):
        session = Mock()
        session.get = AsyncMock(side_effect=[
            httpx2.Response(429, headers={'Retry-After': '2'}),
            httpx2.Response(200)])
        client = AsyncClient('key', session)
        client.configure_get_retries(1, max_delay=3)
        with patch('schwab.client.asynchronous.asyncio.sleep', new_callable=AsyncMock) as sleep:
            response = await client._get_request('/test', {})
        self.assertEqual(response.status_code, 200)
        sleep.assert_awaited_once_with(2.0)

    async def test_sync_does_not_retry_by_default_or_before_long_retry_after(self):
        for configure in (False, True):
            session = Mock()
            session.get.return_value = httpx2.Response(
                429, headers={'Retry-After': '300'})
            client = Client('key', session)
            if configure:
                client.configure_get_retries(1, max_delay=3)
            self.assertEqual(client._get_request('/test', {}).status_code, 429)
            self.assertEqual(session.get.call_count, 1)
