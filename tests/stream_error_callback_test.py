import unittest
from unittest.mock import Mock

from schwab.streaming import StreamClient


class HandlerErrorCallbackTest(unittest.TestCase):
    def test_reports_exception_and_preserves_log(self):
        callback = Mock()
        client = StreamClient(Mock(), on_handler_error=callback)
        error = ValueError('handler failed')
        with self.assertLogs('schwab.streaming', level='ERROR'):
            client._log_handler_exception('handler failed', error)
        callback.assert_called_once_with(error)

    def test_rejects_async_callback(self):
        async def callback(error):
            pass
        with self.assertRaisesRegex(ValueError, 'synchronous'):
            StreamClient(Mock(), on_handler_error=callback)
