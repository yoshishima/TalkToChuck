import unittest
import json
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

class BackgroundRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_refresh_runs_before_order_and_stops_with_session(self):
        import asyncio
        import time
        from unittest.mock import AsyncMock
        from schwab.client import AsyncClient
        session = Mock()
        session.token = {'expires_at': time.time() + 299}
        session.leeway = 300
        refreshed = asyncio.Event()
        async def refresh(token):
            token['expires_at'] = time.time() + 3600
            refreshed.set()
        session.ensure_active_token = AsyncMock(side_effect=refresh)
        session.aclose = AsyncMock()
        client = AsyncClient('key', session)
        client.start_token_refresh()
        client.start_token_refresh()
        await asyncio.wait_for(refreshed.wait(), 1)
        await client.close_async_session()
        session.ensure_active_token.assert_awaited_once()
        session.aclose.assert_awaited_once()
        self.assertTrue(client._refresh_task.done())

    async def test_sync_refresh_worker_stops_before_session_close(self):
        import asyncio
        import threading
        import time
        from schwab.client import Client
        session = Mock()
        session.token = {'expires_at': time.time() + 299}
        session.leeway = 300
        refreshed = threading.Event()
        def refresh(token):
            token['expires_at'] = time.time() + 3600
            refreshed.set()
        session.ensure_active_token.side_effect = refresh
        client = Client('key', session)
        client.start_token_refresh()
        client.start_token_refresh()
        self.assertTrue(await asyncio.to_thread(refreshed.wait, 1))
        await asyncio.to_thread(client.close_session)
        session.ensure_active_token.assert_called_once()
        session.close.assert_called_once()
        self.assertFalse(client._refresh_thread.is_alive())

class RefreshFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_and_async_refresh_failures_use_only_eligible_tokens(self):
        import time
        import httpx2
        from authlib.integrations.httpx_client import OAuth2Client, AsyncOAuth2Client
        from authlib.integrations.base_client.errors import OAuthError
        from schwab.client import Client, AsyncClient
        from schwab.orders.equities import equity_buy_market

        for asynchronous in (False, True):
            with self.subTest(asynchronous=asynchronous):
                status = [503]
                calls = []
                def handle(request):
                    calls.append(str(request.url))
                    if str(request.url) == auth.TOKEN_ENDPOINT:
                        self.assertEqual(request.method, 'POST')
                        self.assertIn(b'grant_type=refresh_token', request.content)
                        return httpx2.Response(status[0], json={'error': 'invalid_grant'})
                    self.assertEqual((request.method, request.url.path),
                                     ('POST', '/trader/v1/accounts/account/orders'))
                    self.assertEqual(request.headers['Authorization'], 'Bearer old')
                    self.assertEqual(json.loads(request.content), {
                        'session': 'NORMAL', 'duration': 'DAY', 'orderType': 'MARKET',
                        'orderStrategyType': 'SINGLE', 'orderLegCollection': [{
                            'instruction': 'BUY', 'quantity': 1,
                            'instrument': {'assetType': 'EQUITY', 'symbol': 'XYZ'}}]})
                    return httpx2.Response(201, headers={'Location': str(request.url) + '/1'})
                session_class = AsyncOAuth2Client if asynchronous else OAuth2Client
                session = session_class('key', client_secret='secret',
                    token={'access_token': 'old', 'refresh_token': 'refresh',
                           'token_type': 'Bearer', 'expires_at': int(time.time()) + 200},
                    token_endpoint=auth.TOKEN_ENDPOINT, leeway=300,
                    transport=httpx2.MockTransport(handle))
                if not asynchronous:
                    auth._serialize_sync_token_refresh(session)
                auth._install_refresh_fallback(session, asynchronous=asynchronous)
                client = (AsyncClient if asynchronous else Client)('key', session)
                async def place():
                    result = client.place_order('account', equity_buy_market('XYZ', 1))
                    return await result if asynchronous else result
                try:
                    self.assertEqual((await place()).status_code, 201)
                    self.assertEqual(len(calls), 2)
                    status[0] = 400
                    with self.assertRaises(OAuthError):
                        await place()
                    self.assertEqual(len(calls), 3)
                    client.configure_refresh_fallback(allow_oauth_errors=True)
                    self.assertEqual((await place()).status_code, 201)
                    session.token['expires_at'] = int(time.time()) + 10
                    with self.assertRaises(OAuthError):
                        await place()
                finally:
                    if asynchronous:
                        await client.close_async_session()
                    else:
                        client.close_session()

class EventLoopOrderTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_client_warns_when_called_on_event_loop(self):
        import httpx2
        from schwab.client import Client
        session = Mock()
        session.post.return_value = httpx2.Response(201)
        client = Client('key', session)
        with self.assertWarnsRegex(RuntimeWarning, 'blocks the event loop'):
            client.place_order('account', {})

    async def test_threaded_order_keeps_loop_responsive(self):
        import asyncio
        import threading
        import httpx2
        from schwab.client import Client
        started, release = threading.Event(), threading.Event()
        session = Mock()
        def post(*args, **kwargs):
            started.set()
            release.wait(2)
            return httpx2.Response(201)
        session.post.side_effect = post
        client = Client('key', session)
        task = asyncio.create_task(client.async_place_order('account', {}))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            self.assertFalse(task.done())
        finally:
            release.set()
        self.assertEqual((await asyncio.wait_for(task, 1)).status_code, 201)
        session.post.assert_called_once()

    async def test_cancelled_waiter_can_still_submit_after_pacing(self):
        import asyncio
        import threading
        import httpx2
        from schwab.client import Client
        from schwab.orders.equities import equity_buy_market

        waiting, submitted = threading.Event(), threading.Event()
        session = Mock()
        def post(*args, **kwargs):
            submitted.set()
            return httpx2.Response(201)
        session.post.side_effect = post

        client = Client('key', session)
        client.configure_order_requests(min_interval=0.01)
        throttle = client._throttle_order
        def enter_throttle(path):
            waiting.set()
            throttle(path)

        with patch.object(client, '_throttle_order', side_effect=enter_throttle):
            with client._order_throttle_lock:
                order = equity_buy_market('XYZ', 1)
                task = asyncio.create_task(client.async_place_order('account', order))
                self.assertTrue(await asyncio.to_thread(waiting.wait, 2))
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                session.post.assert_not_called()
            self.assertTrue(await asyncio.to_thread(submitted.wait, 2))
        session.post.assert_called_once_with(
            'https://api.schwabapi.com/trader/v1/accounts/account/orders', json=order.build())

    async def test_threaded_cancel_preserves_order_id_and_account_arguments(self):
        import httpx2
        from schwab.client import Client
        session = Mock()
        session.delete.return_value = httpx2.Response(200)
        client = Client('key', session)
        await client.async_cancel_order('order-1', 'account-2')
        self.assertIn('/accounts/account-2/orders/order-1', session.delete.call_args.args[0])

class OrderRequestPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_accepted_mutation_with_lost_response_is_never_replayed(self):
        import httpx2
        from schwab.client import Client, AsyncClient
        from schwab.orders.common import first_triggers_second
        from schwab.orders.equities import equity_buy_limit, equity_sell_limit

        def expected_single(side, price):
            return {'session': 'NORMAL', 'duration': 'DAY', 'orderType': 'LIMIT',
                    'price': price, 'orderStrategyType': 'SINGLE', 'orderLegCollection': [{
                        'instruction': side, 'quantity': 2,
                        'instrument': {'assetType': 'EQUITY', 'symbol': 'XYZ'}}]}
        expected = expected_single('BUY', '10.00')
        expected.update(orderStrategyType='TRIGGER',
                        childOrderStrategies=[expected_single('SELL', '12.00')])
        composite = first_triggers_second(equity_buy_limit('XYZ', 2, '10.00'),
                                          equity_sell_limit('XYZ', 2, '12.00'))
        collection = '/trader/v1/accounts/account/orders'
        for asynchronous in (False, True):
            for method in ('POST', 'PUT', 'DELETE'):
                with self.subTest(asynchronous=asynchronous, method=method):
                    accepted = []
                    def handle(request):
                        self.assertEqual(request.url.host, 'api.schwabapi.com')
                        self.assertEqual(request.headers['Authorization'], 'Bearer synthetic')
                        self.assertEqual(request.method, method)
                        self.assertEqual(request.url.path, collection if method == 'POST' else collection + '/7')
                        body = json.loads(request.content) if request.content else None
                        self.assertEqual(body, None if method == 'DELETE' else expected)
                        accepted.append((request.method, body))
                        raise httpx2.ReadTimeout('response lost after acceptance', request=request)

                    session_class = auth.AsyncOAuth2Client if asynchronous else auth.OAuth2Client
                    session = session_class('key', token={'access_token': 'synthetic', 'token_type': 'Bearer'},
                                            transport=httpx2.MockTransport(handle), trust_env=False)
                    client = (AsyncClient if asynchronous else Client)('key', session)
                    client.configure_get_retries(2, backoff=0)
                    client.configure_order_requests(raise_for_status=True)
                    try:
                        with self.assertRaises(httpx2.ReadTimeout):
                            if method == 'POST':
                                call = client.place_order if asynchronous else client.async_place_order
                                await call('account', composite)
                            elif method == 'PUT':
                                call = client.replace_order if asynchronous else client.async_replace_order
                                await call('account', 7, composite)
                            else:
                                call = client.cancel_order if asynchronous else client.async_cancel_order
                                await call(7, 'account')
                        self.assertEqual(len(accepted), 1)
                    finally:
                        if asynchronous:
                            await client.close_async_session()
                        else:
                            client.close_session()

    async def test_order_errors_are_opt_in_and_never_retried(self):
        import httpx2
        from unittest.mock import AsyncMock
        from schwab.client import Client, AsyncClient
        for asynchronous in (False, True):
            with self.subTest(asynchronous=asynchronous):
                request = httpx2.Request('POST', 'https://api.schwabapi.com/trader/v1/accounts/a/orders')
                response = httpx2.Response(503, request=request)
                session = Mock()
                session.post = AsyncMock(return_value=response) if asynchronous else Mock(return_value=response)
                client = (AsyncClient if asynchronous else Client)('key', session)
                async def place():
                    if asynchronous:
                        return await client.place_order('a', {})
                    return await client.async_place_order('a', {})
                self.assertEqual((await place()).status_code, 503)
                client.configure_order_requests(raise_for_status=True)
                with self.assertRaises(httpx2.HTTPStatusError) as caught:
                    await place()
                self.assertIs(caught.exception.response, response)
                self.assertEqual(session.post.call_count, 2)

    async def test_concurrent_order_attempts_are_spaced(self):
        import asyncio
        import httpx2
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from schwab.client import Client, AsyncClient
        import schwab.client.asynchronous as async_module
        import schwab.client.synchronous as sync_module

        class Clock:
            def __init__(self):
                self.now = 0.0
                self.delays = []

            def perf_counter(self):
                return self.now

            def sleep(self, delay):
                self.delays.append(delay)
                # Simulate an early wake on the first sleep. The throttle must
                # check the clock again instead of assuming the full delay.
                self.now += delay / 2 if len(self.delays) == 1 else delay

        for asynchronous in (False, True):
            with self.subTest(asynchronous=asynchronous):
                clock = Clock()
                session = Mock()
                response = httpx2.Response(201)
                if asynchronous:
                    session.post = AsyncMock(return_value=response)
                else:
                    session.post.return_value = response
                client = (AsyncClient if asynchronous else Client)('key', session)
                client.configure_order_requests(min_interval=0.04)
                module = async_module if asynchronous else sync_module

                async def async_sleep(delay):
                    clock.sleep(delay)
                    await asyncio.sleep(0)

                async_proxy = SimpleNamespace(sleep=async_sleep)
                from contextlib import nullcontext
                async_patch = (patch.object(module, 'asyncio', async_proxy)
                               if asynchronous else nullcontext())
                with patch.object(module, 'time', clock), async_patch:
                    tasks = [client.place_order('a', {}) if asynchronous
                             else client.async_place_order('a', {}) for _ in range(3)]
                    await asyncio.gather(*tasks)
                self.assertEqual(session.post.call_count, 3)
                self.assertEqual(len(clock.delays), 3)
                self.assertAlmostEqual(clock.delays[0], 0.04)
                self.assertAlmostEqual(clock.delays[1], 0.02)
                self.assertAlmostEqual(clock.delays[2], 0.04)
                self.assertAlmostEqual(client._last_order_request, 0.08)

class RecentOrderPollingTests(unittest.IsolatedAsyncioTestCase):
    async def test_recent_query_bounds_window_for_sync_and_async(self):
        import datetime
        from unittest.mock import AsyncMock
        from schwab.client import Client, AsyncClient
        for asynchronous in (False, True):
            with self.subTest(asynchronous=asynchronous):
                client = (AsyncClient if asynchronous else Client)('key', Mock())
                client._get_request = AsyncMock() if asynchronous else Mock()
                result = client.get_recent_orders_for_account('acct', lookback_days=0.5, max_results=10)
                if asynchronous:
                    await result
                path, params = client._get_request.call_args.args
                self.assertIn('/accounts/acct/orders', path)
                start = datetime.datetime.fromisoformat(params['fromEnteredTime'].replace('Z', '+00:00'))
                end = datetime.datetime.fromisoformat(params['toEnteredTime'].replace('Z', '+00:00'))
                self.assertAlmostEqual((end - start).total_seconds(), 12 * 3600, delta=0.002)
                self.assertEqual(params['maxResults'], 10)
                for invalid in (0, -1, 366, True, float('nan')):
                    with self.assertRaises(ValueError):
                        client.get_recent_orders_for_account('acct', lookback_days=invalid)

class StringPriceTests(unittest.TestCase):
    def test_string_price_validation_preserves_exact_acceptable_input(self):
        from schwab.orders.generic import OrderBuilder
        for method, field in [('set_price', 'price'), ('set_stop_price', 'stopPrice')]:
            for value in ('190.25', '190.2500', '0.1234', '0.0000', '-0.00'):
                with self.subTest(method=method, value=value):
                    self.assertEqual(getattr(OrderBuilder(), method)(value).build()[field], value)
            for value in ('190.256', '0.12345', '-1.00', 'NaN', 'Infinity', 'invalid'):
                with self.subTest(method=method, value=value), self.assertRaises(ValueError):
                    getattr(OrderBuilder(), method)(value)

    def test_copy_methods_keep_explicit_unusual_price(self):
        from schwab.orders.generic import OrderBuilder
        builder = OrderBuilder().copy_price('190.256').copy_stop_price('0.12345')
        self.assertEqual(builder.build()['price'], '190.256')
        self.assertEqual(builder.build()['stopPrice'], '0.12345')
