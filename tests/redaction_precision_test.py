import json
import unittest

from schwab.debug import LogRedactor, redact_json, register_redactions


class RedactionPrecisionTest(unittest.TestCase):
    def test_market_fields_and_symbols_remain_useful(self):
        payload = {'data': [{'service': 'LEVELONE_EQUITIES', 'key': 'AAPL',
                             'dividendAmount': 100, 'liquidationValue': 100,
                             'side': 'BUY', 'width': 5, 'provider': 'test',
                             'paidDate': 'today', 'holiday': False}]}
        self.assertEqual(payload, json.loads(redact_json(payload, stream=True)))
        request = {'requests': [{'service': 'LEVELONE_EQUITIES',
                                 'parameters': {'keys': 'AAPL,MSFT'}}]}
        self.assertEqual(request, json.loads(redact_json(request, stream=True)))

    def test_stream_account_keys_and_generic_credentials_are_hidden(self):
        payload = {'requests': [{'service': 'ACCT_ACTIVITY',
                                 'parameters': {'keys': 'private-correl-id'}}]}
        self.assertNotIn('private-correl-id', redact_json(payload, stream=True))
        self.assertNotIn('private-key', redact_json({'apiKey': 'private-key'}))

    def test_short_numeric_identifier_does_not_redact_larger_number(self):
        redactor = LogRedactor()
        register_redactions({'accountId': 100}, redactor=redactor)
        self.assertEqual('price 1100.25 id <REDACTED accountId>',
                         redactor.redact('price 1100.25 id 100'))
