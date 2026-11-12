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

class SubscriptionRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_replay_successful_subscriptions_and_removals(self):
        client = StreamClient(Mock())
        client._socket = object()
        send = client._send_request_and_await_response = AsyncMock()
        fields = client.LevelOneEquityFields
        await client.level_one_equity_subs(['A', 'B'], fields=[fields.BID_PRICE])
        await client.level_one_equity_add(['C'], fields=[fields.ASK_PRICE])
        await client.level_one_equity_unsubs(['B'])
        send.reset_mock()
        await client.resubscribe()
        requests = [call.args[0] for call in send.await_args_list]
        self.assertEqual([(r['command'], r['parameters']) for r in requests], [
            ('SUBS', {'keys': 'A', 'fields': '0,1'}),
            ('ADD', {'keys': 'C', 'fields': '0,2'})])

    async def test_replay_uses_current_account_key_and_keeps_registry_on_failure(self):
        client = StreamClient(Mock())
        client._socket = object()
        client._stream_correl_id = 'old'
        send = client._send_request_and_await_response = AsyncMock()
        await client.account_activity_sub()
        client._stream_correl_id = 'new'
        send.side_effect = RuntimeError('failed')
        with self.assertRaises(RuntimeError):
            await client.resubscribe()
        self.assertIn('ACCT_ACTIVITY', client._subscriptions)
        self.assertEqual(send.await_args.args[0]['parameters']['keys'], 'new')
        send.side_effect = None
        await client.resubscribe()
