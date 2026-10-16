from .base import BaseClient


class AsyncClient(BaseClient):

    async def close_async_session(self):
        await self.session.aclose()

    async def _get_request(self, path, params):
        dest = 'https://api.schwabapi.com' + path

        req_num = self._log_request('GET', dest, params)

        resp = await self.session.get(dest, params=params)
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
