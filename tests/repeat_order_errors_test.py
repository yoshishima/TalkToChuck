import contextlib
import io
import unittest
from unittest.mock import patch

import httpx2

from schwab.contrib.orders import UnrepeatableOrderError, construct_repeat_order
from schwab.scripts.orders_codegen import latest_order_main


class RepeatOrderErrorsTest(unittest.TestCase):
    def test_invalid_builder_values_are_unrepeatable(self):
        for field, value in [('activationPrice', 0.0),
                             ('stopPriceOffset', None), ('quantity', 0)]:
            with self.subTest(field=field), self.assertRaises(UnrepeatableOrderError):
                construct_repeat_order({'orderStrategyType': 'SINGLE', field: value})

    def test_cli_reports_setter_validation_error(self):
        order = {'orderStrategyType': 'SINGLE', 'quantity': 0, 'orderId': 1,
                 'enteredTime': '2024-01-02T03:04:05+0000'}
        with patch('schwab.scripts.orders_codegen.client_from_token_file') as client:
            client.return_value.get_orders_for_all_linked_accounts.return_value = (
                httpx2.Response(200, json=[order]))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = latest_order_main([
                    '--token_file', 'token.json', '--api_key', 'synthetic',
                    '--app_secret', 'synthetic'])
        self.assertEqual(-1, result)
        self.assertIn('Cannot generate code for order 1', output.getvalue())
