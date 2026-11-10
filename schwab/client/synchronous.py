from .base import BaseClient
import time


class Client(BaseClient):

    def close_session(self):
        '''Close the underlying HTTP session and its connection pool. The
        synchronous counterpart of :meth:`AsyncClient.close_async_session`.'''
        self.session.close()

    def _get_request(self, path, params):
        dest = 'https://api.schwabapi.com' + path

        req_num = self._log_request('GET', dest, params)

        attempt = 0
        while True:
            resp = self.session.get(dest, params=params)
            delay = self._get_retry_delay(resp, attempt)
            if delay is None:
                break
            resp.close()
            time.sleep(delay)
            attempt += 1
        self._log_response(resp, req_num)
        return resp

    def _post_request(self, path, data):
        dest = 'https://api.schwabapi.com' + path

        req_num = self._log_request('POST', dest, data)

        resp = self.session.post(dest, json=data)
        self._log_response(resp, req_num)
        return resp

    def _put_request(self, path, data):
        dest = 'https://api.schwabapi.com' + path

        req_num = self._log_request('PUT', dest, data)

        resp = self.session.put(dest, json=data)
        self._log_response(resp, req_num)
        return resp

    def _delete_request(self, path):
        dest = 'https://api.schwabapi.com' + path

        req_num = self._log_request('DELETE', dest)

        resp = self.session.delete(dest)
        self._log_response(resp, req_num)
        return resp
