import asyncio
import unittest
from unittest.mock import Mock

from schwab.streaming import StreamClient


class StreamOrderingTest(unittest.IsolatedAsyncioTestCase):
    async def test_service_handlers_are_serialized_without_blocking_other_services(self):
        client = StreamClient(Mock(), sequential_handler_services={'ACCT_ACTIVITY'})
        gate = asyncio.Event()
        events = []

        async def handler(name, wait=False):
            events.append('start ' + name)
            if wait:
                await gate.wait()
            events.append('end ' + name)

        first = client._schedule_handler('ACCT_ACTIVITY', handler('first', True))
        second = client._schedule_handler('ACCT_ACTIVITY', handler('second'))
        other = client._schedule_handler('QUOTE', handler('other'))
        await other
        self.assertEqual(events, ['start first', 'start other', 'end other'])
        gate.set()
        await asyncio.gather(first, second)
        self.assertEqual(events[-3:], ['end first', 'start second', 'end second'])

    async def test_cancelled_waiter_closes_unstarted_coroutine(self):
        client = StreamClient(Mock(), sequential_handler_services={'ACCT_ACTIVITY'})
        async def handler():
            pass
        coroutine = handler()
        task = client._schedule_handler('ACCT_ACTIVITY', coroutine)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.assertIsNone(coroutine.cr_frame)
