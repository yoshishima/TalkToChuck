import logging
import unittest

from schwab.debug import _HTTP_FILTER, get_websocket_logger


class ThirdPartyLoggingTest(unittest.TestCase):
    def test_http_logger_hides_schwab_url_but_not_other_hosts(self):
        for url, expected in [
            ('https://api.schwabapi.com/accounts/secret?token=secret', False),
            ('https://example.com/accounts/secret', True),
        ]:
            record = logging.LogRecord('httpx2', logging.INFO, '', 0,
                'HTTP Request: %s %s "%s %d %s"',
                ('GET', url, 'HTTP/1.1', 200, 'OK'), None)
            _HTTP_FILTER.filter(record)
            self.assertEqual('secret' in record.getMessage(), expected)

    def test_websocket_frames_are_omitted_before_formatting(self):
        logger = get_websocket_logger()
        record = logging.LogRecord(logger.name, logging.DEBUG, '', 0,
                                   '> %s', ('partial-secret',), None)
        self.assertTrue(logger.filter(record))
        self.assertEqual(record.getMessage(), 'WebSocket frame omitted')
