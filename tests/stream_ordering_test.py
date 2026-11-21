import asyncio
import threading
import unittest
from unittest.mock import Mock, patch

from schwab.streaming import StreamClient


class StreamOrderingTest(unittest.IsolatedAsyncioTestCase):
    async def test_example_output_applies_backpressure_and_keeps_loop_responsive(self):
        from examples.streaming.level_one_equity import MyStreamConsumer
        consumer = MyStreamConsumer('synthetic', 'synthetic', 'unused', queue_size=1)
        started, release = threading.Event(), threading.Event()
        output = []
        def slow_output(value):
            self.assertTrue(threading.current_thread() is not threading.main_thread())
            output.append(value)
            started.set()
            self.assertTrue(release.wait(3))

        await consumer.handle_level_one_equity(1)
        second = asyncio.create_task(consumer.handle_level_one_equity(2))
        await asyncio.sleep(0)
        self.assertFalse(second.done())
        with patch('examples.streaming.level_one_equity.pprint.pprint', slow_output):
            worker = asyncio.create_task(consumer.handle_queue())
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                await asyncio.wait_for(second, 2)
                self.assertEqual(consumer.queue.qsize(), 1)
                release.set()
                await asyncio.wait_for(consumer.queue.join(), 2)
            finally:
                release.set()
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
        self.assertEqual(output, [1, 2])

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
