import unittest
from unittest.mock import Mock, patch

from schwab import auth


class ConnectionPoolTests(unittest.TestCase):
    def test_auth_clients_use_bounded_longer_keepalive(self):
        metadata = auth.TokenMetadata({'access_token': 'synthetic'}, 0, Mock())
        for asynchronous, target in [(False, 'OAuth2Client'), (True, 'AsyncOAuth2Client')]:
            with self.subTest(asynchronous=asynchronous), \
                    patch('schwab.auth.' + target) as session_class, \
                    patch('schwab.auth.Client'), patch('schwab.auth.AsyncClient'):
                auth._client_from_token_metadata('key', 'secret', metadata, asynchronous, True)
                limits = session_class.call_args.kwargs['limits']
                self.assertEqual(limits.keepalive_expiry, 60.0)
                self.assertEqual(limits.max_connections, 100)
                self.assertEqual(limits.max_keepalive_connections, 20)
