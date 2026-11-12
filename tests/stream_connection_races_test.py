import asyncio
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from schwab.streaming import StreamClient, StreamClosedError
from .utils import MockResponse, account_preferences


class StreamConnectionRacesTest(unittest.IsolatedAsyncioTestCase):
    async def test_queued_requests_do_not_send_on_replacement_socket(self):
        for batch in (False, True):
            with self.subTest(batch=batch):
                client = StreamClient(Mock())
                old_socket = client._socket = AsyncMock()
                new_socket = AsyncMock()
                await client._send_lock.acquire()
                if batch:
                    operation = client.send_requests([{
                        'service': 'LEVELONE_EQUITIES', 'command': 'SUBS',
                        'parameters': {'keys': 'AAPL', 'fields': '0'},
                    }])
                else:
                    operation = client.level_one_equity_subs(['AAPL'])
                task = asyncio.create_task(operation)
                await asyncio.sleep(0)
                self.assertTrue(client._response_waiters)
                await client._close_connection()
                client._socket = new_socket
                client._send_lock.release()
                with self.assertRaises(StreamClosedError):
                    await task
                old_socket.send.assert_not_awaited()
                new_socket.send.assert_not_awaited()
                self.assertFalse(client._subscriptions)
                await client._close_connection()

    async def test_request_waiting_for_service_lock_stays_on_old_connection(self):
        client = StreamClient(Mock())
        client._socket = AsyncMock()
        lock = client._subscription_locks['LEVELONE_EQUITIES']
        await lock.acquire()
        task = asyncio.create_task(client.level_one_equity_subs(['AAPL']))
        await asyncio.sleep(0)
        await client._close_connection()
        new_socket = client._socket = AsyncMock()
        lock.release()
        with self.assertRaises(StreamClosedError):
            await task
        new_socket.send.assert_not_awaited()
        await client._close_connection()

    async def test_split_subscription_stops_when_connection_changes(self):
        client = StreamClient(Mock(), max_symbols_per_request=1)
        client._socket = AsyncMock()
        new_socket = AsyncMock()

        async def send(*args, **kwargs):
            await client._close_connection()
            client._socket = new_socket

        client._send_request_and_await_response = AsyncMock(side_effect=send)
        with self.assertRaises(StreamClosedError):
            await client.level_one_equity_subs(['AAPL', 'MSFT'])
        self.assertEqual(client._send_request_and_await_response.await_count, 1)
        new_socket.send.assert_not_awaited()
        await client._close_connection()

    async def test_resubscribe_waiting_for_service_lock_stays_on_old_connection(self):
        client = StreamClient(Mock())
        client._socket = AsyncMock()
        client._subscriptions['LEVELONE_EQUITIES'] = {
            'AAPL': (client.LevelOneEquityFields, (0, 1)),
        }
        lock = client._subscription_locks['LEVELONE_EQUITIES']
        await lock.acquire()
        task = asyncio.create_task(client.resubscribe())
        await asyncio.sleep(0)
        await client._close_connection()
        new_socket = client._socket = AsyncMock()
        lock.release()
        with self.assertRaises(StreamClosedError):
            await task
        new_socket.send.assert_not_awaited()
        await client._close_connection()

    async def test_concurrent_logins_close_the_first_socket(self):
        http = Mock()
        http.get_user_preferences.return_value = MockResponse(account_preferences(), 200)
        http.token_metadata.token = {'access_token': 'synthetic'}
        client = StreamClient(http)
        sockets = [AsyncMock(), AsyncMock()]
        first_connect_started = asyncio.Event()
        allow_first_connect = asyncio.Event()
        connect_calls = 0

        for socket in sockets:
            incoming = asyncio.Queue()

            async def send(raw, incoming=incoming):
                request = json.loads(raw)['requests'][0]
                await incoming.put(json.dumps({'response': [{
                    'requestid': request['requestid'],
                    'service': request['service'], 'command': request['command'],
                    'content': {'code': 0},
                }]}))

            socket.send.side_effect = send
            socket.recv.side_effect = incoming.get

        async def connect(*args, **kwargs):
            nonlocal connect_calls
            index = connect_calls
            connect_calls += 1
            if index == 0:
                first_connect_started.set()
                await allow_first_connect.wait()
            return sockets[index]

        with patch('schwab.streaming.ws_client.connect', side_effect=connect):
            first = asyncio.create_task(client.login())
            await first_connect_started.wait()
            second = asyncio.create_task(client.login())
            await asyncio.sleep(0)
            self.assertEqual(connect_calls, 1)
            allow_first_connect.set()
            await asyncio.wait_for(asyncio.gather(first, second), 1)

        sockets[0].close.assert_awaited_once()
        self.assertIs(client._socket, sockets[1])
        await client._close_connection()
        sockets[1].close.assert_awaited_once()

    async def test_failed_old_login_does_not_close_replacement_socket(self):
        http = Mock()
        http.get_user_preferences.return_value = MockResponse(account_preferences(), 200)
        http.token_metadata.token = {'access_token': 'synthetic'}
        client = StreamClient(http)
        old_socket = AsyncMock()
        await client._send_lock.acquire()
        with patch('schwab.streaming.ws_client.connect',
                   new=AsyncMock(return_value=old_socket)):
            task = asyncio.create_task(client.login())
            await asyncio.sleep(0)
            self.assertIs(client._socket, old_socket)
            await client._close_connection()
            new_socket = client._socket = AsyncMock()
            client._send_lock.release()
            with self.assertRaises(StreamClosedError):
                await task
        self.assertIs(client._socket, new_socket)
        new_socket.send.assert_not_awaited()
        new_socket.close.assert_not_awaited()
        await client._close_connection()

    async def test_old_logout_does_not_close_replacement_socket(self):
        client = StreamClient(Mock())
        client._socket = AsyncMock()
        started = asyncio.Event()
        finish = asyncio.Event()

        async def delayed_logout(*args, **kwargs):
            started.set()
            await finish.wait()

        client._send_request_and_await_response = AsyncMock(side_effect=delayed_logout)
        task = asyncio.create_task(client.logout())
        await started.wait()
        await client._close_connection()
        new_socket = client._socket = AsyncMock()
        finish.set()
        await task
        self.assertIs(client._socket, new_socket)
        new_socket.close.assert_not_awaited()
        await client._close_connection()
