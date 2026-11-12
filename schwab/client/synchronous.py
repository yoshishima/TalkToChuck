from .base import BaseClient
import asyncio
import time
import threading
import warnings
import httpx2


class Client(BaseClient):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._refresh_control_lock = threading.Lock()
        self._refresh_stop = threading.Event()
        self._refresh_thread = None

    def start_token_refresh(self):
        '''Start an optional background token refresh worker for this client.'''
        with self._refresh_control_lock:
            if self._refresh_thread is not None and self._refresh_thread.is_alive():
                return
            self._refresh_stop.clear()
            self._refresh_thread = threading.Thread(
                target=self._refresh_loop, name='schwab-token-refresh', daemon=True)
            self._refresh_thread.start()

    def _refresh_loop(self):
        while not self._refresh_stop.wait(self._background_refresh_delay()):
            try:
                self.session.ensure_active_token(self.session.token)
            except Exception as exc:
                self.logger.warning('Background token refresh failed (%s)',
                                    type(exc).__name__)
                if self._refresh_stop.wait(30.0):
                    break
            else:
                if self._background_refresh_delay() == 0 and self._refresh_stop.wait(30.0):
                    break

    def stop_token_refresh(self):
        '''Stop the worker, waiting for any in-flight refresh to finish.'''
        with self._refresh_control_lock:
            self._refresh_stop.set()
            worker = self._refresh_thread
        if worker is not None and worker is not threading.current_thread():
            worker.join()

    def close_session(self):
        '''Close the underlying HTTP session and its connection pool. The
        synchronous counterpart of :meth:`AsyncClient.close_async_session`.'''
        self.stop_token_refresh()
        self.session.close()

    @staticmethod
    def _warn_if_event_loop_running():
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        warnings.warn(
            'A synchronous Schwab client blocks the event loop; use AsyncClient '
            'or run the call in a worker thread.', RuntimeWarning, stacklevel=4)

    async def async_place_order(self, account_hash, order_spec):
        '''Run place_order in a worker thread without blocking the event loop.'''
        return await asyncio.to_thread(self.place_order, account_hash, order_spec)

    async def async_replace_order(self, account_hash, order_id, order_spec):
        '''Run replace_order in a worker thread without blocking the event loop.'''
        return await asyncio.to_thread(self.replace_order, account_hash, order_id,
                                       order_spec)

    async def async_cancel_order(self, order_id, account_hash):
        '''Run cancel_order in a worker thread without blocking the event loop.'''
        return await asyncio.to_thread(self.cancel_order, order_id, account_hash)

    def _get_request(self, path, params):
        self._warn_if_event_loop_running()
        dest = 'https://api.schwabapi.com' + path

        req_num = self._log_request('GET', dest, params)

        attempt = 0
        while True:
            try:
                resp = self.session.get(dest, params=params)
            except (httpx2.NetworkError, httpx2.TimeoutException) as exc:
                delay = self._get_transport_retry_delay(exc, dest, attempt)
                if delay is None:
                    raise
                time.sleep(delay)
                attempt += 1
                continue
            delay = self._get_retry_delay(resp, attempt)
            if delay is None:
                break
            resp.close()
            time.sleep(delay)
            attempt += 1
        self._log_response(resp, req_num)
        return resp

    def _post_request(self, path, data):
        self._warn_if_event_loop_running()
        dest = 'https://api.schwabapi.com' + path

        req_num = self._log_request('POST', dest, data)

        resp = self.session.post(dest, json=data)
        self._log_response(resp, req_num)
        return resp

    def _put_request(self, path, data):
        self._warn_if_event_loop_running()
        dest = 'https://api.schwabapi.com' + path

        req_num = self._log_request('PUT', dest, data)

        resp = self.session.put(dest, json=data)
        self._log_response(resp, req_num)
        return resp

    def _delete_request(self, path):
        self._warn_if_event_loop_running()
        dest = 'https://api.schwabapi.com' + path

        req_num = self._log_request('DELETE', dest)

        resp = self.session.delete(dest)
        self._log_response(resp, req_num)
        return resp
