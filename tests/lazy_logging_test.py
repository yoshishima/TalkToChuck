import logging
import unittest

from schwab.debug import get_redacted_logger
from schwab.utils import LazyLog
import schwab


class LazyLoggingTest(unittest.TestCase):
    def test_discarded_debug_record_is_not_rendered(self):
        logger = get_redacted_logger('schwab.test.lazy_filter')
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        handler = logging.NullHandler(level=logging.INFO)
        logger.addHandler(handler)
        rendered = []
        try:
            logger.debug('%s', LazyLog(lambda: rendered.append(True) or 'text'))
            self.assertEqual(rendered, [])
        finally:
            logger.removeHandler(handler)

    def test_emitted_lazy_message_is_redacted(self):
        logger = get_redacted_logger('schwab.test.lazy_secret')
        secret = 'secret-for-lazy-logging-test'
        schwab.LOG_REDACTOR.register(secret, 'TOKEN')
        with self.assertLogs(logger, level='DEBUG') as captured:
            logger.debug('token: %s', LazyLog(lambda: secret))
        self.assertNotIn(secret, captured.output[0])
