import json
import os
import tempfile
import unittest
from unittest.mock import patch

from schwab import auth


class TokenReplaceRetryTest(unittest.TestCase):
    def test_transient_windows_permission_error_is_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'token.json')
            replace = os.replace
            with patch.object(auth.os, 'replace') as mocked:
                def attempt(source, destination):
                    effect = next(attempt.effects)
                    if effect:
                        raise effect
                    replace(source, destination)
                attempt.effects = iter([PermissionError('busy'), None])
                mocked.side_effect = attempt
                with patch.object(auth.os, 'name', 'nt'), patch.object(auth.time, 'sleep'):
                    getattr(auth, '__make_update_token_func')(path)({'token': 'new'})
            with open(path) as token_file:
                self.assertEqual({'token': 'new'}, json.load(token_file))
            self.assertEqual(2, mocked.call_count)

    def test_persistent_failure_is_bounded_and_preserves_old_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'token.json')
            with open(path, 'w') as token_file:
                token_file.write('old')
            with patch.object(auth.os, 'replace', side_effect=PermissionError), \
                    patch.object(auth.os, 'name', 'nt'), patch.object(auth.time, 'sleep'):
                with self.assertRaises(PermissionError):
                    getattr(auth, '__make_update_token_func')(path)({'token': 'new'})
            with open(path) as token_file:
                self.assertEqual('old', token_file.read())
            self.assertEqual(['token.json'], os.listdir(directory))
