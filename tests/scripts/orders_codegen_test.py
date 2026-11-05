import callee
import httpx2 as httpx
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import call, MagicMock, patch

from ..utils import AnyStringWith, no_duplicates
from schwab.scripts.orders_codegen import latest_order_main, _latest_order


class LatestOrderSelectionTest(unittest.TestCase):
    def test_uses_entered_time_not_order_id_and_handles_timezones(self):
        orders = [
            {'orderId': 900, 'enteredTime': '2024-01-01T00:00:00Z'},
            {'orderId': 100, 'enteredTime': '2025-01-01T01:00:00+0100'},
            {'orderId': 101, 'enteredTime': '2024-12-31T19:00:00-0500'},
        ]
        self.assertEqual(101, _latest_order(orders)['orderId'])

    def test_missing_or_ambiguous_timestamps_cannot_select_wrong_order(self):
        for entered_time in (None, 'invalid', '2025-01-01T12:00:00'):
            with self.subTest(entered_time=entered_time):
                orders = [{'orderId': 1, 'enteredTime': entered_time},
                          {'orderId': 2, 'enteredTime': '2025-01-01T00:00:00Z'}]
                with self.assertRaises(ValueError):
                    _latest_order(orders)

    def test_single_order_needs_no_timestamp(self):
        order = {'orderId': 1}
        self.assertIs(order, _latest_order([order]))

    def test_cli_generates_code_for_latest_timestamp(self):
        orders = [
            {'orderId': 900, 'enteredTime': '2024-01-01T00:00:00Z'},
            {'orderId': 100, 'enteredTime': '2025-01-01T00:00:00Z'},
        ]
        client = MagicMock()
        client.get_orders_for_all_linked_accounts.return_value = httpx.Response(
            200, json=orders)
        args = ['--token_file', 'unused', '--api_key', 'synthetic',
                '--app_secret', 'synthetic']
        with patch('schwab.scripts.orders_codegen.client_from_token_file',
                   return_value=client), \
                patch('schwab.scripts.orders_codegen.construct_repeat_order') as construct, \
                patch('schwab.scripts.orders_codegen.code_for_builder'), \
                patch('builtins.print'):
            self.assertEqual(0, latest_order_main(args))
        construct.assert_called_once_with(orders[1])

    def test_cli_does_not_generate_code_if_order_time_is_unknown(self):
        client = MagicMock()
        client.get_orders_for_all_linked_accounts.return_value = httpx.Response(
            200, json=[{'orderId': 1}, {'orderId': 2}])
        args = ['--token_file', 'unused', '--api_key', 'synthetic',
                '--app_secret', 'synthetic']
        with patch('schwab.scripts.orders_codegen.client_from_token_file',
                   return_value=client), \
                patch('schwab.scripts.orders_codegen.construct_repeat_order') as construct, \
                patch('builtins.print') as output:
            self.assertEqual(-1, latest_order_main(args))
        construct.assert_not_called()
        self.assertIn('Unable to determine', str(output.call_args))

class LatestOrderTest(unittest.TestCase):

    def setUp(self):
        self.args = []

    def add_arg(self, arg):
        self.args.append(arg)

    def main(self):
        return latest_order_main(self.args)

    @no_duplicates
    @patch('builtins.print')
    @patch('schwab.scripts.orders_codegen.client_from_token_file')
    @patch('schwab.scripts.orders_codegen.construct_repeat_order')
    @patch('schwab.scripts.orders_codegen.code_for_builder')
    def test_success_no_account_id(
            self,
            mock_code_for_builder,
            mock_construct_repeat_order,
            mock_client_from_token_file,
            mock_print):
        self.add_arg('--token_file')
        self.add_arg('filename.json')
        self.add_arg('--api_key')
        self.add_arg('api-key')
        self.add_arg('--app_secret')
        self.add_arg('app-secret')

        orders = [
                {'orderId': 201, 'enteredTime': '2025-01-02T00:00:00Z'},
                {'orderId': 101, 'enteredTime': '2025-01-01T00:00:00Z'},
                {'orderId': 301, 'enteredTime': '2025-01-03T00:00:00Z'},
                {'orderId': 401, 'enteredTime': '2025-01-04T00:00:00Z'},
        ]

        mock_client = MagicMock()
        mock_client_from_token_file.return_value = mock_client
        mock_client.get_orders_for_all_linked_accounts.return_value \
                = httpx.Response(200, json=orders)

        self.assertEqual(self.main(), 0)

        mock_client_from_token_file.assert_called_once_with(
                'filename.json', 'api-key', 'app-secret')
        mock_construct_repeat_order.assert_called_once_with(orders[3])
        mock_print.assert_has_calls([
                call('# Order ID', 401),
                call(mock_code_for_builder.return_value)])


    @no_duplicates
    @patch('builtins.print')
    @patch('schwab.scripts.orders_codegen.client_from_token_file')
    @patch('schwab.scripts.orders_codegen.construct_repeat_order')
    @patch('schwab.scripts.orders_codegen.code_for_builder')
    def test_no_account_id_no_recent_orders(
            self,
            mock_code_for_builder,
            mock_construct_repeat_order,
            mock_client_from_token_file,
            mock_print):
        self.add_arg('--token_file')
        self.add_arg('filename.json')
        self.add_arg('--api_key')
        self.add_arg('api-key')
        self.add_arg('--app_secret')
        self.add_arg('app-secret')

        orders = []

        mock_client = MagicMock()
        mock_client_from_token_file.return_value = mock_client
        mock_client.get_orders_for_all_linked_accounts.return_value \
                = httpx.Response(200, json=orders)

        self.assertEqual(self.main(), 0)

        mock_construct_repeat_order.assert_not_called()
        mock_print.assert_called_once_with('No recent orders found')


    @no_duplicates
    @patch('builtins.print')
    @patch('schwab.scripts.orders_codegen.client_from_token_file')
    @patch('schwab.scripts.orders_codegen.construct_repeat_order')
    @patch('schwab.scripts.orders_codegen.code_for_builder')
    def test_no_account_id_error(
            self,
            mock_code_for_builder,
            mock_construct_repeat_order,
            mock_client_from_token_file,
            mock_print):
        self.add_arg('--token_file')
        self.add_arg('filename.json')
        self.add_arg('--api_key')
        self.add_arg('api-key')
        self.add_arg('--app_secret')
        self.add_arg('app-secret')

        orders = {'errors': [{'message': 'invalid'}]}

        mock_client = MagicMock()
        mock_client_from_token_file.return_value = mock_client
        mock_client.get_orders_for_all_linked_accounts.return_value \
                = httpx.Response(200, json=orders)

        self.assertEqual(self.main(), -1)

        mock_construct_repeat_order.assert_not_called()
        mock_print.assert_called_once_with(
                AnyStringWith('Schwab returned error:'))


    @no_duplicates
    @patch('builtins.print')
    @patch('schwab.scripts.orders_codegen.client_from_token_file')
    @patch('schwab.scripts.orders_codegen.construct_repeat_order')
    @patch('schwab.scripts.orders_codegen.code_for_builder')
    def test_account_id_success(
            self,
            mock_code_for_builder,
            mock_construct_repeat_order,
            mock_client_from_token_file,
            mock_print):
        self.add_arg('--token_file')
        self.add_arg('filename.json')
        self.add_arg('--api_key')
        self.add_arg('api-key')
        self.add_arg('--app_secret')
        self.add_arg('app-secret')
        self.add_arg('--account_id')
        self.add_arg('123456')

        orders = [
                {'orderId': 201, 'enteredTime': '2025-01-02T00:00:00Z'},
                {'orderId': 101, 'enteredTime': '2025-01-01T00:00:00Z'},
                {'orderId': 301, 'enteredTime': '2025-01-03T00:00:00Z'},
                {'orderId': 401, 'enteredTime': '2025-01-04T00:00:00Z'},
        ]

        mock_client = MagicMock()
        mock_client_from_token_file.return_value = mock_client
        mock_client.get_account_numbers.return_value = httpx.Response(
                200,
                json=[{
                    'accountNumber': '123456',
                    'hashValue': 'hash-value',
                }])
        mock_client.get_orders_for_account.return_value \
                = httpx.Response(200, json=orders)

        self.assertEqual(self.main(), 0)

        mock_client.get_account_numbers.assert_called_once()
        mock_construct_repeat_order.assert_called_once_with(orders[3])
        mock_print.assert_has_calls([
                call('# Order ID', 401),
                call(mock_code_for_builder.return_value)])


    @no_duplicates
    @patch('builtins.print')
    @patch('schwab.scripts.orders_codegen.client_from_token_file')
    @patch('schwab.scripts.orders_codegen.construct_repeat_order')
    @patch('schwab.scripts.orders_codegen.code_for_builder')
    def test_account_id_no_corresponding_hash(
            self,
            mock_code_for_builder,
            mock_construct_repeat_order,
            mock_client_from_token_file,
            mock_print):
        self.add_arg('--token_file')
        self.add_arg('filename.json')
        self.add_arg('--api_key')
        self.add_arg('api-key')
        self.add_arg('--app_secret')
        self.add_arg('app-secret')
        self.add_arg('--account_id')
        self.add_arg('123456')

        orders = [
                {'orderId': 201, 'enteredTime': '2025-01-02T00:00:00Z'},
                {'orderId': 101, 'enteredTime': '2025-01-01T00:00:00Z'},
                {'orderId': 301, 'enteredTime': '2025-01-03T00:00:00Z'},
                {'orderId': 401, 'enteredTime': '2025-01-04T00:00:00Z'},
        ]

        mock_client = MagicMock()
        mock_client_from_token_file.return_value = mock_client
        mock_client.get_account_numbers.return_value = httpx.Response(
                200,
                json=[{
                    'accountNumber': '90009',
                    'hashValue': 'hash-value',
                }])
        mock_client.get_orders_for_account.return_value \
                = httpx.Response(200, json=orders)

        self.assertEqual(self.main(), -1)

        mock_client.get_account_numbers.assert_called_once()
        mock_print.assert_called_once_with(
                AnyStringWith('Failed to find account hash for account ID'))
        output = str(mock_print.call_args)
        self.assertNotIn('123456', output)
        self.assertNotIn('90009', output)
        self.assertNotIn('hash-value', output)


    @no_duplicates
    @patch('builtins.print')
    @patch('schwab.scripts.orders_codegen.client_from_token_file')
    @patch('schwab.scripts.orders_codegen.construct_repeat_order')
    @patch('schwab.scripts.orders_codegen.code_for_builder')
    def test_account_id_no_orders(
            self,
            mock_code_for_builder,
            mock_construct_repeat_order,
            mock_client_from_token_file,
            mock_print):
        self.add_arg('--token_file')
        self.add_arg('filename.json')
        self.add_arg('--api_key')
        self.add_arg('api-key')
        self.add_arg('--app_secret')
        self.add_arg('app-secret')
        self.add_arg('--account_id')
        self.add_arg('123456')

        orders = []

        mock_client = MagicMock()
        mock_client_from_token_file.return_value = mock_client
        mock_client.get_account_numbers.return_value = httpx.Response(
                200,
                json=[{
                    'accountNumber': '123456',
                    'hashValue': 'hash-value',
                }])
        mock_client.get_orders_for_account.return_value \
                = httpx.Response(200, json=orders)

        self.assertEqual(self.main(), 0)

        mock_construct_repeat_order.assert_not_called
        mock_print.assert_called_once_with('No recent orders found')


    @no_duplicates
    @patch('builtins.print')
    @patch('schwab.scripts.orders_codegen.client_from_token_file')
    @patch('schwab.scripts.orders_codegen.construct_repeat_order')
    @patch('schwab.scripts.orders_codegen.code_for_builder')
    def test_account_id_error(
            self,
            mock_code_for_builder,
            mock_construct_repeat_order,
            mock_client_from_token_file,
            mock_print):
        self.add_arg('--token_file')
        self.add_arg('filename.json')
        self.add_arg('--api_key')
        self.add_arg('api-key')
        self.add_arg('--app_secret')
        self.add_arg('app-secret')
        self.add_arg('--account_id')
        self.add_arg('123456')

        mock_client = MagicMock()
        mock_client_from_token_file.return_value = mock_client
        mock_client.get_account_numbers.return_value = httpx.Response(
                200,
                json=[{
                    'accountNumber': '123456',
                    'hashValue': 'hash-value',
                }])
        mock_client.get_orders_for_account.return_value \
                = httpx.Response(200, json={'error': 'invalid'})

        self.assertEqual(self.main(), -1)

        mock_construct_repeat_order.assert_not_called
        mock_print.assert_called_once_with(
                AnyStringWith('Schwab returned error: "invalid"'))


    @no_duplicates
    @patch('builtins.print')
    @patch('schwab.scripts.orders_codegen.client_from_token_file')
    @patch('schwab.scripts.orders_codegen.construct_repeat_order')
    @patch('schwab.scripts.orders_codegen.code_for_builder')
    def test_success_account_hash(
            self,
            mock_code_for_builder,
            mock_construct_repeat_order,
            mock_client_from_token_file,
            mock_print):
        self.add_arg('--token_file')
        self.add_arg('filename.json')
        self.add_arg('--api_key')
        self.add_arg('api-key')
        self.add_arg('--app_secret')
        self.add_arg('app-secret')
        self.add_arg('--account_hash')
        self.add_arg('account-hash')

        orders = [
                {'orderId': 201, 'enteredTime': '2025-01-02T00:00:00Z'},
                {'orderId': 101, 'enteredTime': '2025-01-01T00:00:00Z'},
                {'orderId': 301, 'enteredTime': '2025-01-03T00:00:00Z'},
                {'orderId': 401, 'enteredTime': '2025-01-04T00:00:00Z'},
        ]

        mock_client = MagicMock()
        mock_client_from_token_file.return_value = mock_client
        mock_client.get_orders_for_account.return_value \
                = httpx.Response(200, json=orders)

        self.assertEqual(self.main(), 0)

        mock_construct_repeat_order.assert_called_once_with(orders[3])
        mock_print.assert_has_calls([
                call('# Order ID', 401),
                call(mock_code_for_builder.return_value)])


    @no_duplicates
    @patch('builtins.print')
    @patch('schwab.scripts.orders_codegen.client_from_token_file')
    @patch('schwab.scripts.orders_codegen.construct_repeat_order')
    @patch('schwab.scripts.orders_codegen.code_for_builder')
    def test_order_fetching_fails_no_account_id(
            self,
            mock_code_for_builder,
            mock_construct_repeat_order,
            mock_client_from_token_file,
            mock_print):
        self.add_arg('--token_file')
        self.add_arg('filename.json')
        self.add_arg('--api_key')
        self.add_arg('api-key')
        self.add_arg('--app_secret')
        self.add_arg('app-secret')

        mock_client = MagicMock()
        mock_client_from_token_file.return_value = mock_client
        mock_client.get_orders_for_all_linked_accounts.return_value \
                = httpx.Response(400)

        self.assertEqual(self.main(), -1)

        mock_print.assert_called_once_with(
                AnyStringWith('Returned HTTP status code 400'))


    @no_duplicates
    @patch('builtins.print')
    @patch('schwab.scripts.orders_codegen.client_from_token_file')
    @patch('schwab.scripts.orders_codegen.construct_repeat_order')
    @patch('schwab.scripts.orders_codegen.code_for_builder')
    def test_order_fetching_fails_account_id(
            self,
            mock_code_for_builder,
            mock_construct_repeat_order,
            mock_client_from_token_file,
            mock_print):
        self.add_arg('--token_file')
        self.add_arg('filename.json')
        self.add_arg('--api_key')
        self.add_arg('api-key')
        self.add_arg('--app_secret')
        self.add_arg('app-secret')
        self.add_arg('--account_id')
        self.add_arg('123456')

        mock_client = MagicMock()
        mock_client_from_token_file.return_value = mock_client
        mock_client.get_account_numbers.return_value = httpx.Response(
                200,
                json=[{
                    'accountNumber': '123456',
                    'hashValue': 'hash-value',
                }])
        mock_client.get_orders_for_account.return_value = httpx.Response(400)

        self.assertEqual(self.main(), -1)

        mock_print.assert_called_once_with(
                AnyStringWith('Returned HTTP status code 400'))


    @no_duplicates
    @patch('builtins.print')
    @patch('schwab.scripts.orders_codegen.client_from_token_file')
    @patch('schwab.scripts.orders_codegen.construct_repeat_order')
    @patch('schwab.scripts.orders_codegen.code_for_builder')
    def test_warn_on_non_auto_requestedDestination(
            self,
            mock_code_for_builder,
            mock_construct_repeat_order,
            mock_client_from_token_file,
            mock_print):
        self.add_arg('--token_file')
        self.add_arg('filename.json')
        self.add_arg('--api_key')
        self.add_arg('api-key')
        self.add_arg('--app_secret')
        self.add_arg('app-secret')
        self.add_arg('--account_hash')
        self.add_arg('account-hash')

        orders = [
                {'orderId': 401,
                 'requestedDestination': 'not AUTO'},
        ]

        mock_client = MagicMock()
        mock_client_from_token_file.return_value = mock_client
        mock_client.get_orders_for_account.return_value \
                = httpx.Response(200, json=orders)

        self.assertEqual(self.main(), 0)

        mock_construct_repeat_order.assert_called_once_with(orders[0])
        mock_print.assert_has_calls([
                call(callee.Contains('requestedDestination')),
                call(callee.Contains('broken')),
                call(),
                call('# Order ID', 401),
                call(mock_code_for_builder.return_value)])


    @no_duplicates
    @patch('builtins.print')
    @patch('schwab.scripts.orders_codegen.client_from_token_file')
    @patch('schwab.scripts.orders_codegen.construct_repeat_order')
    @patch('schwab.scripts.orders_codegen.code_for_builder')
    def test_warn_on_non_auto_destinationLinkName(
            self,
            mock_code_for_builder,
            mock_construct_repeat_order,
            mock_client_from_token_file,
            mock_print):
        self.add_arg('--token_file')
        self.add_arg('filename.json')
        self.add_arg('--api_key')
        self.add_arg('api-key')
        self.add_arg('--app_secret')
        self.add_arg('app-secret')
        self.add_arg('--account_hash')
        self.add_arg('account-hash')

        orders = [
                {'orderId': 401,
                 'destinationLinkName': 'not AUTO'},
        ]

        mock_client = MagicMock()
        mock_client_from_token_file.return_value = mock_client
        mock_client.get_orders_for_account.return_value \
                = httpx.Response(200, json=orders)

        self.assertEqual(self.main(), 0)

        mock_construct_repeat_order.assert_called_once_with(orders[0])
        mock_print.assert_has_calls([
                call(callee.Contains('destinationLinkName')),
                call(callee.Contains('preserved in the generated code')),
                call(),
                call('# Order ID', 401),
                call(mock_code_for_builder.return_value)])


class ScriptInvocationTest(unittest.TestCase):

    def test_get_help(self):
        output = subprocess.check_output(
                [sys.executable, str(Path(__file__).resolve().parents[2]
                                     / 'bin' / 'schwab-order-codegen.py'),
                 '--help'], text=True)
        self.assertIn('--account_hash', output)
        self.assertIn('--token_file', output)
