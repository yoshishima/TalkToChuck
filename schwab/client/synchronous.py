from .base import BaseClient


class Client(BaseClient):
    def _get_request(self, path, params):
        dest = 'https://api.schwabapi.com' + path

        req_num = self._log_request('GET', dest, params)

        resp = self.session.get(dest, params=params)
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
