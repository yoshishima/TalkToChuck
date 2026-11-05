import atexit
import io
import json
import logging
import schwab
import unittest

from schwab.client import Client
from .utils import MockResponse, no_duplicates
from unittest.mock import Mock, patch


class RedactorTest(unittest.TestCase):

    def setUp(self):
        self.redactor = schwab.debug.LogRedactor()

    @no_duplicates
    def test_no_redactions(self):
        self.assertEqual('test message', self.redactor.redact('test message'))

    @no_duplicates
    def test_simple_redaction(self):
        self.redactor.register('secret', 'SECRET')

        self.assertEqual(
            '<REDACTED SECRET> message',
            self.redactor.redact('secret message'))

    @no_duplicates
    def test_multiple_registrations_same_string(self):
        self.redactor.register('secret', 'SECRET')
        self.redactor.register('secret', 'SECRET')

        self.assertEqual(
            '<REDACTED SECRET> message',
            self.redactor.redact('secret message'))

    @no_duplicates
    def test_multiple_registrations_same_string_different_label(self):
        self.redactor.register('secret-A', 'SECRET')
        self.redactor.register('secret-B', 'SECRET')

        self.assertEqual(
            '<REDACTED SECRET-1> message <REDACTED SECRET-2>',
            self.redactor.redact('secret-A message secret-B'))

    @no_duplicates
    def test_sorted_redactions_are_cached_until_registration(self):
        self.redactor.register('secret-A', 'SECRET')
        with patch('builtins.sorted', wraps=sorted) as sorted_mock:
            self.redactor.redact('first line')
            self.redactor.redact('second line')
            self.assertEqual(1, sorted_mock.call_count)

            self.redactor.register('secret-B', 'SECRET')
            self.redactor.redact('third line')
            self.assertEqual(2, sorted_mock.call_count)

    @no_duplicates
    def test_transient_redactions_are_bounded_and_clearable(self):
        redactor = schwab.debug.LogRedactor(max_transient_entries=2)
        redactor.register('api-key', 'API_KEY')
        self.assertTrue(redactor.register(
                'order-1', 'orderId', persistent=False))
        self.assertTrue(redactor.register(
                'order-2', 'orderId', persistent=False))
        self.assertFalse(redactor.register(
                'order-3', 'orderId', persistent=False))
        self.assertEqual(3, len(redactor.redacted_strings))
        self.assertTrue(redactor.transient_limit_reached)

        redactor.clear_transient()
        self.assertEqual({'api-key'}, set(redactor.redacted_strings))
        self.assertFalse(redactor.transient_limit_reached)

    @no_duplicates
    def test_empty_transient_values_do_not_exhaust_limit(self):
        redactor = schwab.debug.LogRedactor(max_transient_entries=0)
        self.assertTrue(redactor.register(None, 'orderId', persistent=False))
        self.assertTrue(redactor.register('', 'orderId', persistent=False))
        self.assertFalse(redactor.transient_limit_reached)


class OrdinaryLoggingTest(unittest.TestCase):
    def test_sensitive_parent_covers_nested_objects_and_whitelisted_names(self):
        payload = {
            'authentication': {'credential': {'value': 'private-credential'}},
            'accountNumber': [{'requestId': 'private-account'}],
            'requestId': 'public-request',
        }
        with patch.object(schwab, 'LOG_REDACTOR', schwab.debug.LogRedactor()):
            sanitized = json.loads(schwab.debug.redact_json(payload))
        self.assertNotIn('private-credential', json.dumps(sanitized))
        self.assertNotIn('private-account', json.dumps(sanitized))
        self.assertEqual('public-request', sanitized['requestId'])
        self.assertEqual('private-credential',
                         payload['authentication']['credential']['value'])

    def test_structured_redaction_accepts_numeric_object_keys(self):
        payload = {123: {'accountNumber': 'private-account'}}
        with patch.object(schwab, 'LOG_REDACTOR', schwab.debug.LogRedactor()):
            output = schwab.debug.redact_json(payload)
        self.assertNotIn('private-account', output)
        self.assertIn('REDACTED', output)

    def test_response_bodies_are_omitted_from_root_handler(self):
        session = Mock()
        client = Client('test-api-key', session)
        response = MockResponse({
            'accountNumber': 'private-account',
            'currentBalances': {'cashBalance': 987654.32},
        }, 200)
        with patch('schwab.debug._BUG_REPORT_LOGGING_ACTIVE', 0), \
                patch.object(client.logger, 'level', logging.DEBUG), \
                self.assertLogs(level='DEBUG') as logs:
            client._log_response(response, 1)
        output = '\n'.join(logs.output)
        self.assertNotIn('private-account', output)
        self.assertNotIn('987654.32', output)
        self.assertIn('response body omitted', output)

    def test_bug_report_redacts_before_propagating_to_root(self):
        client = Client('test-api-key', Mock())
        secret = 'private-"account\\\u00e9'
        response = MockResponse({'accountNumber': secret}, 200)
        with patch.object(schwab, 'LOG_REDACTOR', schwab.debug.LogRedactor()):
            writer = schwab.debug._enable_bug_report_logging(output=io.StringIO())
            try:
                with self.assertLogs(level='DEBUG') as logs:
                    client._log_response(response, 1)
                output = '\n'.join(logs.output)
                self.assertNotIn(secret, output)
                self.assertNotIn(json.dumps(secret), output)
                self.assertIn('REDACTED', output)
            finally:
                writer()

    def test_registered_secrets_redacted_before_root_handler(self):
        with patch.object(schwab, 'LOG_REDACTOR', schwab.debug.LogRedactor()):
            schwab.LOG_REDACTOR.register('private-token', 'TOKEN')
            logger = schwab.debug.get_redacted_logger('schwab.test')
            with self.assertLogs(level='DEBUG') as logs:
                logger.debug('Credential: %s', 'private-token')
            self.assertNotIn('private-token', '\n'.join(logs.output))
            self.assertEqual((), logs.records[0].args)

    def test_structured_redaction_does_not_mutate_or_retain_payload(self):
        payload = {'Authorization': 'private-token',
                   'nested': {'accountNumber': 987654321}}
        with patch.object(schwab, 'LOG_REDACTOR', schwab.debug.LogRedactor()):
            output = schwab.debug.redact_json(payload)
            self.assertNotIn('private-token', output)
            self.assertNotIn('987654321', output)
            self.assertEqual('private-token', payload['Authorization'])
            self.assertEqual(987654321, payload['nested']['accountNumber'])
            self.assertFalse(schwab.LOG_REDACTOR.redacted_strings)


class RegisterRedactionsTest(unittest.TestCase):

    def setUp(self):
        self.captured = io.StringIO()
        self.logger = logging.getLogger('test')
        self.dump_logs = schwab.debug._enable_bug_report_logging(
            output=self.captured, loggers=[self.logger])
        schwab.LOG_REDACTOR = schwab.debug.LogRedactor()

    def tearDown(self):
        self.dump_logs()

    @no_duplicates
    def test_empty_string(self):
        schwab.debug.register_redactions('')

    @no_duplicates
    def test_empty_dict(self):
        schwab.debug.register_redactions({})

    @no_duplicates
    def test_empty_list(self):
        schwab.debug.register_redactions([])

    @no_duplicates
    def test_dict(self):
        schwab.debug.register_redactions(
            {'BadNumber': '100001'},
            bad_patterns=['bad'])
        schwab.debug.register_redactions(
            {'OtherBadNumber': '200002'},
            bad_patterns=['bad'])

        self.logger.info('Bad Number: 100001')
        self.logger.info('Other Bad Number: 200002')

        self.dump_logs()
        self.assertRegex(
            self.captured.getvalue(),
            r'\[.*\] Bad Number: <REDACTED BadNumber>\n' +
            r'\[.*\] Other Bad Number: <REDACTED OtherBadNumber>\n')

    @no_duplicates
    def test_list_of_dict(self):
        schwab.debug.register_redactions(
            [{'GoodNumber': '900009'},
             {'BadNumber': '100001'},
             {'OtherBadNumber': '200002'}],
            bad_patterns=['bad'])

        self.logger.info('Good Number: 900009')
        self.logger.info('Bad Number: 100001')
        self.logger.info('Other Bad Number: 200002')

        self.dump_logs()
        self.assertRegex(
            self.captured.getvalue(),
            r'\[.*\] Good Number: 900009\n' +
            r'\[.*\] Bad Number: <REDACTED 1-BadNumber>\n' +
            r'\[.*\] Other Bad Number: <REDACTED 2-OtherBadNumber>\n')

    @no_duplicates
    def test_whitelist(self):
        schwab.debug.register_redactions(
            [{'GoodNumber': '900009'},
             {'BadNumber': '100001'},
             {'OtherBadNumber': '200002'}],
            bad_patterns=['bad'],
            whitelisted=['otherbadnumber'])

        self.logger.info('Good Number: 900009')
        self.logger.info('Bad Number: 100001')
        self.logger.info('Other Bad Number: 200002')

        self.dump_logs()
        self.assertRegex(
            self.captured.getvalue(),
            r'\[.*\] Good Number: 900009\n' +
            r'\[.*\] Bad Number: <REDACTED 1-BadNumber>\n' +
            r'\[.*\] Other Bad Number: 200002\n')

    @no_duplicates
    def test_schwab_account_identifiers_and_names(self):
        sensitive_values = {
            'accountNumber': '123456789',
            'hashValue': 'ABCDEF012345',
            'firstName': 'Ada',
            'lastName': 'Lovelace',
            'nickName': 'Primary Brokerage',
        }
        schwab.debug.register_redactions(sensitive_values)

        redacted = schwab.LOG_REDACTOR.redact(' '.join(
                sensitive_values.values()))
        for value in sensitive_values.values():
            self.assertNotIn(value, redacted)

    @no_duplicates
    @patch('schwab.debug.register_redactions', new_callable=Mock)
    def test_register_from_request_success(self, register_redactions):
        resp = MockResponse({'success': 1}, 200)
        schwab.debug.register_redactions_from_response(resp)
        register_redactions.assert_called_with(
                {'success': 1}, persistent=False)

    @no_duplicates
    @patch('schwab.debug.register_redactions', new_callable=Mock)
    def test_register_from_request_error_response(self, register_redactions):
        resp = MockResponse({'success': 1}, 403)
        schwab.debug.register_redactions_from_response(resp)
        register_redactions.assert_called_with(
                {'success': 1}, persistent=False)

    @no_duplicates
    @patch('schwab.debug.register_redactions', new_callable=Mock)
    def test_register_unparseable_json(self, register_redactions):
        class MR(MockResponse):
            def json(self):
                raise json.decoder.JSONDecodeError('e243rschwabgew', '', 0)

        resp = MR({'success': 1}, 200)
        schwab.debug.register_redactions_from_response(resp)
        register_redactions.assert_not_called()

class EnableDebugLoggingTest(unittest.TestCase):

    @patch('schwab.debug._enable_bug_report_logging')
    def test_enable_doesnt_throw_exceptions(self, enable):
        schwab.debug.enable_bug_report_logging()
        enable.assert_called_once_with()

    @no_duplicates
    def test_log_writer_is_idempotent(self):
        output = io.StringIO()
        logger = logging.getLogger('idempotent-log-test')
        write_logs = schwab.debug._enable_bug_report_logging(
                output=output, loggers=[logger])
        logger.info('message')

        write_logs()
        write_logs()

        self.assertEqual(output.getvalue().count('BEGIN REDACTED LOGS'), 1)

    @no_duplicates
    def test_log_writer_tolerates_closed_output(self):
        output = io.StringIO()
        write_logs = schwab.debug._enable_bug_report_logging(
                output=output, loggers=[])
        output.close()
        write_logs()

    @no_duplicates
    def test_log_writer_tolerates_broken_pipe(self):
        class BrokenPipeOutput(io.StringIO):
            def write(self, value):
                raise BrokenPipeError('reader closed')

        write_logs = schwab.debug._enable_bug_report_logging(
                output=BrokenPipeOutput(), loggers=[])
        write_logs()

    @no_duplicates
    def test_log_buffer_is_bounded(self):
        output = io.StringIO()
        logger = logging.getLogger('bounded-log-test')
        write_logs = schwab.debug._enable_bug_report_logging(
                output=output, loggers=[logger],
                max_log_messages=2, max_log_chars=10_000)
        logger.info('first message')
        logger.info('second message')
        logger.info('third message')

        write_logs()

        logs = output.getvalue()
        self.assertNotIn('first message', logs)
        self.assertIn('second message', logs)
        self.assertIn('third message', logs)
        self.assertIn('1 older log messages omitted', logs)

    @no_duplicates
    def test_writer_clears_transient_redactions_and_restores_level(self):
        output = io.StringIO()
        logger = logging.getLogger('cleanup-log-test')
        original_level = logger.level
        redactor = schwab.debug.LogRedactor()
        redactor.register('api-key', 'API_KEY')
        redactor.register('order-id', 'orderId', persistent=False)

        with patch.object(schwab, 'LOG_REDACTOR', redactor):
            write_logs = schwab.debug._enable_bug_report_logging(
                    output=output, loggers=[logger])
            self.assertEqual(logging.DEBUG, logger.level)
            write_logs()

        self.assertEqual(original_level, logger.level)
        self.assertEqual({'api-key'}, set(redactor.redacted_strings))
