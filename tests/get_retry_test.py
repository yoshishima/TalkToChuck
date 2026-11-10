import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx2

from schwab.client.synchronous import Client
from schwab.client.asynchronous import AsyncClient


class GetRetryTest(unittest.IsolatedAsyncioTestCase):
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
