import unittest
from unittest.mock import Mock

from schwab.streaming import StreamClient, _PendingMessage


class StreamCopyTest(unittest.IsolatedAsyncioTestCase):
    async def test_shared_messages_are_opt_in(self):
        for copy_messages in (True, False):
            client = StreamClient(Mock(), copy_messages=copy_messages)
            seen = []
            client._handlers['TEST'].extend([seen.append, seen.append])
            await client._dispatch_handlers(
                'TEST', {'content': [{'value': 1}]},
                dispatch_state=_PendingMessage({}))
            self.assertEqual(seen[0] is seen[1], not copy_messages)
            self.assertEqual(seen[0], seen[1])
