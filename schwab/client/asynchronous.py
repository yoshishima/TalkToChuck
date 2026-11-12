from .base import BaseClient
import asyncio
import time
import httpx2


class AsyncClient(BaseClient):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._refresh_stop = None
        self._refresh_task = None
        self._order_throttle_lock = asyncio.Lock()

    def start_token_refresh(self):
        '''Start an optional background refresh task on the current loop.'''
        if self._refresh_task is not None and not self._refresh_task.done():
            return
        self._refresh_stop = asyncio.Event()
        self._refresh_task = asyncio.create_task(AsyncClient._refresh_loop(self))

    async def _refresh_loop(self):
        while not self._refresh_stop.is_set():
            try:
                await asyncio.wait_for(self._refresh_stop.wait(),
                                       timeout=self._background_refresh_delay())
                return
            except TimeoutError:
                pass
            try:
                await self.session.ensure_active_token(self.session.token)
            except Exception as exc:
                self.logger.warning('Background token refresh failed (%s)',
                                    type(exc).__name__)
                try:
                    await asyncio.wait_for(self._refresh_stop.wait(), timeout=30.0)
                    return
                except TimeoutError:
                    pass
            else:
                if self._background_refresh_delay() == 0:
                    try:
                        await asyncio.wait_for(self._refresh_stop.wait(), timeout=30.0)
                        return
                    except TimeoutError:
                        pass

    async def stop_token_refresh(self):
        '''Stop the worker after any in-flight refresh finishes.'''
        task = self._refresh_task
        if task is None:
            return
        self._refresh_stop.set()
        if task is not asyncio.current_task():
            await asyncio.shield(task)

    async def close_async_session(self):
        await AsyncClient.stop_token_refresh(self)
        await self.session.aclose()

    async def _throttle_order(self, path):
        if not self._is_order_mutation(path) or not self._order_min_interval:
            return
        async with self._order_throttle_lock:
            now = time.monotonic()
            if self._last_order_request is not None:
                delay = self._last_order_request + self._order_min_interval - now
                if delay > 0:
                    await asyncio.sleep(delay)
            self._last_order_request = time.monotonic()

    async def _get_request(self, path, params):
        dest = 'https://api.schwabapi.com' + path

        req_num = self._log_request('GET', dest, params)

        attempt = 0
        while True:
            try:
                resp = await self.session.get(dest, params=params)
            except (httpx2.NetworkError, httpx2.TimeoutException) as exc:
                delay = self._get_transport_retry_delay(exc, dest, attempt)
                if delay is None:
                    raise
                await asyncio.sleep(delay)
                attempt += 1
                continue
            delay = self._get_retry_delay(resp, attempt)
            if delay is None:
                break
            await resp.aclose()
            await asyncio.sleep(delay)
            attempt += 1
        self._log_response(resp, req_num)
        return resp

    async def _post_request(self, path, data):
        await AsyncClient._throttle_order(self, path)
        dest = 'https://api.schwabapi.com' + path

        req_num = self._log_request('POST', dest, data)

        resp = await self.session.post(dest, json=data)
        self._log_response(resp, req_num)
        self._check_order_response(path, resp)
        return resp

    async def _put_request(self, path, data):
        await AsyncClient._throttle_order(self, path)
        dest = 'https://api.schwabapi.com' + path

        req_num = self._log_request('PUT', dest, data)

        resp = await self.session.put(dest, json=data)
        self._log_response(resp, req_num)
        self._check_order_response(path, resp)
        return resp

    async def _delete_request(self, path):
        await AsyncClient._throttle_order(self, path)
        dest = 'https://api.schwabapi.com' + path

        req_num = self._log_request('DELETE', dest)

        resp = await self.session.delete(dest)
        self._log_response(resp, req_num)
        self._check_order_response(path, resp)
        return resp
