import builtins
import threading
import unittest
from unittest.mock import patch

from schwab.debug import LogRedactor


class RedactorThreadTest(unittest.TestCase):
    def test_registration_cannot_be_lost_during_cache_rebuild(self):
        redactor = LogRedactor()
        redactor.register('old-secret', 'old')
        snapshot_ready = threading.Event()
        release_snapshot = threading.Event()
        registration_done = threading.Event()
        real_sorted = builtins.sorted

        def pause_after_snapshot(*args, **kwargs):
            result = real_sorted(*args, **kwargs)
            if threading.current_thread().name == 'redact-worker':
                snapshot_ready.set()
                if not release_snapshot.wait(5):
                    raise AssertionError('cache rebuild did not resume')
            return result

        def register():
            redactor.register('new-secret', 'new')
            registration_done.set()

        with patch('builtins.sorted', side_effect=pause_after_snapshot):
            reader = threading.Thread(name='redact-worker',
                                      target=redactor.redact, args=('old-secret',))
            writer = threading.Thread(target=register)
            reader.start()
            try:
                self.assertTrue(snapshot_ready.wait(5))
                writer.start()
                self.assertFalse(registration_done.wait(0.05))
            finally:
                release_snapshot.set()
                reader.join(5)
                if writer.ident is not None:
                    writer.join(5)
        self.assertTrue(registration_done.is_set())
        self.assertNotIn('new-secret', redactor.redact('new-secret'))
