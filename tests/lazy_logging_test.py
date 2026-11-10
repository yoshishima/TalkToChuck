import logging
import unittest

from schwab.debug import get_redacted_logger, LogRedactor
from schwab.utils import LazyLog
import schwab


class LazyLoggingTest(unittest.TestCase):
    def test_large_secret_matcher_preserves_longest_first_redaction(self):
        redactor = LogRedactor(max_transient_entries=1000)
        for index in range(200):
            redactor.register('unused-secret-{}'.format(index), 'OTHER',
                              persistent=False)
        redactor.register('abcde', 'LONG', persistent=False)
        redactor.register('bcdefgh', 'LONGER', persistent=False)
        redactor.register('abc', 'SHORT', persistent=False)
        self.assertEqual(redactor.redact('abcdefgh'),
                         'a<REDACTED LONGER>')
        redactor.register('another-secret', 'NEW', persistent=False)
        self.assertEqual(redactor.redact('another-secret'), '<REDACTED NEW>')

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
