from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import Mock

from schwab.client.synchronous import Client


class RequestCounterTest(unittest.TestCase):
    def test_concurrent_request_numbers_are_unique(self):
        client = Client('key', Mock())
        with ThreadPoolExecutor(max_workers=16) as executor:
            numbers = list(executor.map(lambda _: client._req_num(), range(2000)))
        self.assertEqual(set(numbers), set(range(1, 2001)))
