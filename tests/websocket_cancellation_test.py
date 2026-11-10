import asyncio
import unittest

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve


class WebsocketCancellationTest(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_recv_during_fragmented_message_preserves_order(self):
        first_sent = asyncio.Event()
        continue_send = asyncio.Event()

        async def server(socket):
            async def fragments():
                yield 'first-'
                first_sent.set()
                await continue_send.wait()
                yield 'message'
            await socket.send(fragments())
            await socket.send('second-message')

        async with serve(server, '127.0.0.1', 0) as listener:
            port = listener.sockets[0].getsockname()[1]
            async with connect('ws://127.0.0.1:{}'.format(port)) as socket:
                pending = asyncio.create_task(socket.recv())
                await asyncio.wait_for(first_sent.wait(), 2)
                await asyncio.sleep(0)
                pending.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await pending
                continue_send.set()
                self.assertEqual(await asyncio.wait_for(socket.recv(), 2),
                                 'first-message')
                self.assertEqual(await asyncio.wait_for(socket.recv(), 2),
                                 'second-message')
