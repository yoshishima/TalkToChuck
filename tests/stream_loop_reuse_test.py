import asyncio
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from schwab.streaming import StreamClient
from .utils import MockResponse, account_preferences


class StreamLoopReuseTest(unittest.TestCase):
    def test_relogin_on_new_loop_rebuilds_loop_bound_state(self):
        http = Mock()
        http.get_user_preferences.return_value = MockResponse(account_preferences(), 200)
        http.token_metadata.token = {'access_token': 'synthetic'}
        stream = StreamClient(http)

        async def run():
            socket = AsyncMock()
            incoming = asyncio.Queue()

            async def send(raw):
                request = json.loads(raw)['requests'][0]
                await incoming.put(json.dumps({'response': [{
                    'requestid': request['requestid'], 'service': request['service'],
                    'command': request['command'], 'content': {'code': 0}}]}))

            socket.send.side_effect = send
            socket.recv.side_effect = incoming.get
            with patch('schwab.streaming.ws_client.connect', new=AsyncMock(return_value=socket)):
                await stream.login()
                reader = stream._reader_task
                await asyncio.sleep(0)
                self.assertIs(reader, stream._reader_task)
                self.assertFalse(reader.done())
                await stream.logout()

        asyncio.run(run())
        asyncio.run(run())

    def test_reader_error_without_waiter_is_logged(self):
        stream = StreamClient(Mock())

        async def run():
            stream._reader_wakeup.wait = AsyncMock(side_effect=RuntimeError('failed'))
            stream.logger = Mock()
            await stream._reader_loop()
            stream.logger.exception.assert_called_once()

        asyncio.run(run())
