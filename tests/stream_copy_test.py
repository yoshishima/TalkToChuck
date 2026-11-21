import asyncio
import unittest
from collections import OrderedDict
from unittest.mock import Mock

from schwab.streaming import StreamClient, _PendingMessage


class StreamCopyTest(unittest.IsolatedAsyncioTestCase):
    async def test_deep_book_yields_and_preserves_field_order_and_isolation(self):
        handler = StreamClient._BookHandler(lambda m: None, StreamClient.BookFields)
        exchanges = [{'0': 'EX', 'extra': [1], '2': i} for i in range(512)]
        raw = {'content': [{'2': [{'0': 10, '3': exchanges}]}]}
        copy_task = asyncio.create_task(handler.label_message_cooperatively(raw))
        await asyncio.sleep(0)
        self.assertFalse(copy_task.done())
        labeled = await copy_task
        self.assertEqual(handler.label_message(raw), labeled)
        exchange = labeled['content'][0]['BIDS'][0]['BIDS'][0]
        self.assertEqual(list(exchange), ['extra', 'EXCHANGE', 'QUOTE_TIME_MILLIS'])
        exchange['extra'].append(2)
        self.assertEqual(exchanges[0]['extra'], [1])

    async def test_book_copy_preserves_custom_nested_containers(self):
        class Levels(list):
            pass
        handler = StreamClient._BookHandler(lambda m: None, StreamClient.BookFields)
        raw = {'content': [{'3': Levels([OrderedDict([('0', 10), ('2', 1)])])}]}
        result = await handler.label_message_cooperatively(raw)
        asks = result['content'][0]['ASKS']
        self.assertIsInstance(asks, Levels)
        self.assertIsInstance(asks[0], OrderedDict)
        self.assertEqual(asks[0], OrderedDict([('ASK_PRICE', 10), ('NUM_ASKS', 1)]))

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
