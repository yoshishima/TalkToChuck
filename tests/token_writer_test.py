import unittest

from schwab.auth import TokenMetadata, client_from_received_url


class TokenWriterTest(unittest.IsolatedAsyncioTestCase):
    async def test_async_refresh_awaits_writer_and_propagates_errors(self):
        writes = []

        async def writer(token):
            writes.append(token)
            if token['token'] == 'bad':
                raise RuntimeError('storage failed')

        metadata = TokenMetadata('old', 123, writer)
        update = metadata.async_wrapped_token_write_func()
        await update('new')
        self.assertEqual(writes, [{'token': 'new', 'creation_timestamp': 123}])
        with self.assertRaisesRegex(RuntimeError, 'storage failed'):
            await update('bad')

    async def test_synchronous_paths_reject_async_writers(self):
        async def writer(token):
            pass

        metadata = TokenMetadata('old', 123, writer)
        with self.assertRaisesRegex(TypeError, 'synchronous token writer'):
            metadata.wrapped_token_write_func()('new')
        with self.assertRaisesRegex(TypeError, 'synchronous token writer'):
            client_from_received_url('key', 'secret', None, 'url', writer)
