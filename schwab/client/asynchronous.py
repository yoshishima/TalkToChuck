from .base import BaseClient
import asyncio
import httpx2


class AsyncClient(BaseClient):

    async def close_async_session(self):
        await self.session.aclose()

    async def _get_request(self, path, params):
        dest = 'https://api.schwabapi.com' + path

        req_num = self._log_request('GET', dest, params)

        attempt = 0
        while True:
            try:
                resp = await self.session.get(dest, params=params)
            except (httpx2.NetworkError, httpx2.TimeoutException):
                delay = self._get_retry_delay(None, attempt)
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
        dest = 'https://api.schwabapi.com' + path

        req_num = self._log_request('POST', dest, data)

        resp = await self.session.post(dest, json=data)
        self._log_response(resp, req_num)
        return resp

    async def _put_request(self, path, data):
        dest = 'https://api.schwabapi.com' + path

        req_num = self._log_request('PUT', dest, data)

        resp = await self.session.put(dest, json=data)
        self._log_response(resp, req_num)
        return resp

    async def _delete_request(self, path):
        dest = 'https://api.schwabapi.com' + path

        req_num = self._log_request('DELETE', dest)

        resp = await self.session.delete(dest)
        self._log_response(resp, req_num)
        return resp
