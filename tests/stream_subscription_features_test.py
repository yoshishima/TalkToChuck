import unittest
from unittest.mock import AsyncMock, Mock

from schwab.streaming import StreamClient


class SubscriptionFieldTests(unittest.IsolatedAsyncioTestCase):
    async def test_add_reuses_successful_subscription_fields_per_service(self):
        client = StreamClient(Mock())
        client._socket = object()
        client._send_request_and_await_response = AsyncMock()
        field = client.LevelOneEquityFields.BID_PRICE
        await client.level_one_equity_subs(['AAPL'], fields=[field])
        await client.level_one_equity_add(['MSFT'])
        request = client._send_request_and_await_response.await_args.args[0]
        self.assertEqual(request['parameters']['fields'], '0,1')

    async def test_failed_subscription_does_not_replace_previous_fields(self):
        client = StreamClient(Mock())
        client._socket = object()
        send = client._send_request_and_await_response = AsyncMock()
        await client.level_one_equity_subs(['AAPL'], fields=[client.LevelOneEquityFields.BID_PRICE])
        send.side_effect = [RuntimeError('failed'), None]
        with self.assertRaises(RuntimeError):
            await client.level_one_equity_subs(['AAPL'], fields=[client.LevelOneEquityFields.ASK_PRICE])
        await client.level_one_equity_add(['MSFT'])
        self.assertEqual(send.await_args.args[0]['parameters']['fields'], '0,1')
