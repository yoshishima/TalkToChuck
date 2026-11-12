from authlib.integrations.httpx_client import AsyncOAuth2Client, OAuth2Client

import collections
import asyncio as asyncio_module
import contextlib
import httpx2 as httpx
import inspect
import json
import math
import multiprocess
import os
import psutil
import queue
import secrets
import stat
import sys
import tempfile
import threading
import time
import urllib.parse
import webbrowser
from typing import Any, Callable

from schwab.client import AsyncClient, Client
from schwab.debug import register_redactions, get_redacted_logger


TOKEN_ENDPOINT = 'https://api.schwabapi.com/v1/oauth/token'
_CALLBACK_SERVER_STARTUP_TIMEOUT = 30.0
_CALLBACK_SERVER_PROBE_TIMEOUT = 1.0
_CALLBACK_SERVER_POLL_INTERVAL = 0.1


def get_logger():
    return get_redacted_logger(__name__)


def __make_update_token_func(token_path):
    def update_token(t, *args, **kwargs):
        get_logger().info('Updating token to file %s', token_path)

        # Resolve the final path before the atomic replacement. Replacing the
        # path passed by the caller directly would replace a symlink itself and
        # leave its target holding a stale token.
        resolved_token_path = os.path.realpath(os.path.abspath(token_path))
        token_dir = os.path.dirname(resolved_token_path)
        fd, temporary_path = tempfile.mkstemp(
                dir=token_dir, prefix='.schwab-token-', text=True)
        try:
            if hasattr(os, 'fchmod'):
                os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
            with os.fdopen(fd, 'w') as f:
                fd = None
                json.dump(t, f)
                f.flush()
                os.fsync(f.fileno())
            # Windows readers that do not share delete access can briefly
            # prevent the atomic rename (for example, while antivirus scans).
            for attempt in range(5):
                try:
                    os.replace(temporary_path, resolved_token_path)
                    break
                except PermissionError:
                    if os.name != 'nt' or attempt == 4:
                        raise
                    time.sleep(0.05 * (attempt + 1))
            os.chmod(
                    resolved_token_path, stat.S_IRUSR | stat.S_IWUSR)
            _fsync_directory(token_dir)
        except Exception:
            if fd is not None:
                os.close(fd)
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
            raise
    update_token._schwab_file_writer = True
    return update_token


def _fsync_directory(path):
    '''Persist a rename on POSIX filesystems. Best effort: some filesystems
    and platforms (including Windows) do not support syncing directories.'''
    if os.name != 'posix':
        return
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _check_token_path_writable(token_path):
    '''Fail before an interactive login if the token cannot be saved.

    The authorization code can only be exchanged once, so a token that cannot
    be written after the exchange is lost and the login must be repeated.
    '''
    directory = os.path.dirname(os.path.realpath(os.path.abspath(token_path)))
    try:
        fd, probe = tempfile.mkstemp(dir=directory, prefix='.schwab-token-check-')
    except OSError as e:
        e.add_note('schwab-py checks that token_path {!r} can be written before '
                   'starting the login flow.'.format(token_path))
        raise
    os.close(fd)
    os.unlink(probe)


def __token_loader(token_path):
    def load_token():
        get_logger().info('Loading token from file %s', token_path)

        with open(token_path, 'rb') as f:
            return json.load(f)
    return load_token


class TokenMetadata:
    '''
    Provides the functionality required to maintain and update our view of the
    token's metadata.
    '''
    def __init__(self, token, creation_timestamp, unwrapped_token_write_func):
        '''
        :param token: The token to wrap in metadata
        :param creation_timestamp: Timestamp at which this token was initially 
                                   created. Notably, this timestamp does not 
                                   change when the token is updated.
        :unwrapped_token_write_func: Function that accepts a non-metadata
                                     wrapped token and writes it to disk or 
                                     other persistent storage.
        '''

        self.creation_timestamp = creation_timestamp

        # The token write function is ultimately stored in the session. When we
        # get a new token we immediately wrap it in a new sesssion. We hold on
        # to the unwrapped token writer function to allow us to inject the
        # appropriate write function.
        self.unwrapped_token_write_func = unwrapped_token_write_func
        self.offload_sync_token_writer = False

        # The current token. Updated whenever the wrapped token update function 
        # is called.
        self.token = token

    @classmethod
    def from_loaded_token(cls, token, unwrapped_token_write_func):
        '''
        Returns a new ``TokenMetadata`` object extracted from the metadata of
        the loaded token object. If the token has a legacy format which contains
        no metadata, assign default values.
        '''
        if not isinstance(token, dict):
            raise ValueError(
                    'The token must be a JSON object; the token file is '
                    'corrupted. Please delete it and create a new one.')
        if 'creation_timestamp' not in token:
            raise ValueError(
                    'WARNING: The token format has changed since this token '+
                    'was created. Please delete it and create a new one.')
        creation_timestamp = token['creation_timestamp']
        if (type(creation_timestamp) not in (int, float)
                or not math.isfinite(creation_timestamp)):
            raise ValueError(
                    'The token creation_timestamp must be a number; the token '
                    'file is corrupted. Please delete it and create a new one.')
        if not isinstance(token.get('token'), dict):
            raise ValueError(
                    'The token is missing its OAuth token object; the token '
                    'file is corrupted. Please delete it and create a new one.')

        return TokenMetadata(
                token['token'],
                token['creation_timestamp'],
                unwrapped_token_write_func)

    def token_age(self):
        '''Returns the number of second elapsed since this token was initially 
        created.'''
        return int(time.time()) - self.creation_timestamp

    def wrapped_token_write_func(self):
        '''
        Returns a version of the unwrapped write function which wraps the token 
        in metadata and updates our view on the most recent token.

        Persistence errors propagate, but the in-memory token remains current
        so subsequent operations use the same token as the OAuth session.
        '''
        def wrapped_token_write_func(token, *args, **kwargs):
            # Authlib already uses this token. Keep our in-memory view current
            # even if persistence fails, while still surfacing the write error.
            self.token = token
            register_redactions(token)
            result = self.unwrapped_token_write_func(
                self.wrap_token_in_metadata(token), *args, **kwargs)
            if inspect.isawaitable(result):
                close = getattr(result, 'close', None)
                if close is not None:
                    close()
                raise TypeError('a synchronous token writer is required')
            return result

        return wrapped_token_write_func

    def async_wrapped_token_write_func(self):
        '''Serialize refresh persistence; offload the built-in file writer only.'''
        lock = asyncio_module.Lock()

        async def write(token, *args, **kwargs):
            async with lock:
                self.token = token
                register_redactions(token)
                writer = self.unwrapped_token_write_func
                payload = self.wrap_token_in_metadata(token)
                if (getattr(writer, '_schwab_file_writer', False) is True
                        or self.offload_sync_token_writer):
                    result = await asyncio_module.to_thread(
                        writer, payload, *args, **kwargs)
                else:
                    result = writer(payload, *args, **kwargs)
                if inspect.isawaitable(result):
                    await result

        async def update_token(token, *args, **kwargs):
            # A canceled refresh must not leave an older file write racing a
            # later refresh. Finish persistence before releasing its lock.
            task = asyncio_module.create_task(write(token, *args, **kwargs))
            try:
                await asyncio_module.shield(task)
            except asyncio_module.CancelledError:
                await asyncio_module.shield(task)
                raise

        return update_token

    def wrap_token_in_metadata(self, token):
        return {
            'creation_timestamp': self.creation_timestamp,
            'token': token,
        }


def _client_from_token_metadata(api_key, app_secret, metadata, asyncio,
                                enforce_enums):
    '''Share refresh configuration for loaded and newly issued tokens.'''
    register_redactions(metadata.token)
    if asyncio:
        update_token = metadata.async_wrapped_token_write_func()
        session_class = AsyncOAuth2Client
        client_class = AsyncClient
    else:
        update_token = metadata.wrapped_token_write_func()
        session_class = OAuth2Client
        client_class = Client

    session = session_class(api_key,
                            client_secret=app_secret,
                            token=metadata.token,
                            token_endpoint=TOKEN_ENDPOINT,
                            update_token=update_token,
                            leeway=300,
                            limits=httpx.Limits(max_connections=100,
                                                max_keepalive_connections=20,
                                                keepalive_expiry=60.0))
    if not asyncio:
        _serialize_sync_token_refresh(session)

    return client_class(
        api_key,
        session,
        token_metadata=metadata,
        enforce_enums=enforce_enums)


def _serialize_sync_token_refresh(session):
    '''Let only one thread at a time check and refresh a synchronous token.

    Authlib serializes refreshes for its async client but not for its sync
    client, so threads sharing one client could each refresh an expiring token
    and persist the results out of order. Waiting threads re-check the
    session's current token, so only the first one refreshes.
    '''
    ensure_active_token = session.ensure_active_token
    # Re-entrant: a custom token writer may itself call the API.
    lock = threading.RLock()

    def serialized_ensure_active_token(token=None):
        with lock:
            return ensure_active_token(session.token)

    session.ensure_active_token = serialized_ensure_active_token
    return session


################################################################################
# client_from_login_flow


def _get_callback_path(encoded_path):
    '''Return a literal Flask route while preserving the OAuth redirect URI.'''
    path = urllib.parse.unquote(encoded_path or '/', errors='strict')
    # Flask interprets angle brackets as route variables and normalizes
    # repeated slashes. Reject paths we cannot route literally.
    if ('<' in path or '>' in path or '//' in path
            or any(ord(char) < 32 for char in path)
            or path == '/schwab-py-internal/status'):
        raise ValueError('Unsupported callback URL path')
    return path


def _run_callback_server(app, callback_port):
    if sys.platform != 'darwin':
        app.run(port=callback_port, ssl_context='adhoc')
        return

    import socketserver
    from werkzeug.serving import ThreadedWSGIServer

    class LoopbackServer(ThreadedWSGIServer):
        def server_bind(self):
            # HTTPServer normally reverse-resolves this address before
            # listening. That lookup can stall on macOS even for loopback.
            # This server is always bound to the validated literal address.
            socketserver.TCPServer.server_bind(self)
            self.server_name = '127.0.0.1'
            self.server_port = self.server_address[1]

    with LoopbackServer('127.0.0.1', callback_port, app,
                        ssl_context='adhoc') as server:
        server.serve_forever()


# This runs in a separate process and is invisible to coverage
def __run_client_from_login_flow_server(
        q, callback_port, callback_path, readiness_token,
        expected_state=None):  # pragma: no cover
    '''Helper server for intercepting redirects to the callback URL. See
    client_from_login_flow for details.'''

    import flask

    app = flask.Flask(__name__)

    @app.route(callback_path)
    def handle_token():
        # Schwab echoes the OAuth state on success and error redirects alike.
        # Ignore anything else, such as a stray local request, so it cannot
        # end the login flow before the real redirect arrives.
        if not _state_matches(flask.request.args.get('state'), expected_state):
            return ('schwab-py ignored a request without the expected OAuth '
                    'state. Finish logging in from the browser window that '
                    'schwab-py opened.', 400)
        q.put(flask.request.url)
        return 'schwab-py callback received! You may now close this window/tab.'

    @app.route('/schwab-py-internal/status')
    def status():
        return readiness_token

    if callback_port == 443:
        return

    # Wrap this call in some hackery to suppress the flask startup messages
    with open(os.devnull, 'w') as devnull:
        import logging
        log = logging.getLogger('werkzeug')
        log.setLevel(logging.ERROR)

        old_stdout = sys.stdout
        sys.stdout = devnull
        _run_callback_server(app, callback_port)
        sys.stdout = old_stdout


def _state_matches(received_state, expected_state):
    if expected_state is None:
        return True
    if received_state is None:
        return False
    return secrets.compare_digest(received_state.encode('utf-8'),
                                  expected_state.encode('utf-8'))


class RedirectTimeoutError(Exception):
    pass


class RedirectServerError(Exception):
    '''Base error raised while starting the local OAuth callback server.'''


class RedirectServerExitedError(RedirectServerError):
    pass


class RedirectServerTimeoutError(RedirectServerError):
    '''Raised when the local OAuth callback server does not become ready.'''


class RedirectServerIdentityError(RedirectServerError):
    '''Raised when another process answers on the OAuth callback port.'''


def _wait_for_callback_server(server, callback_port, readiness_token):
    deadline = time.monotonic() + _CALLBACK_SERVER_STARTUP_TIMEOUT

    while True:
        if server.exitcode is not None:
            hint = (' Check whether the callback URL has an explicit port.'
                    if callback_port == 443 else
                    ' The port may already be in use; choose an unused port.')
            raise RedirectServerExitedError(
                    'Redirect server exited during startup on port {} '
                    '(exit code {}).{}'.format(callback_port, server.exitcode, hint))

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RedirectServerTimeoutError(
                    'Timed out waiting for the local OAuth callback server '
                    'to start on port {}.'.format(callback_port))

        try:
            # The callback server uses a self-signed certificate. Its identity
            # is proven by the readiness token instead of TLS verification.
            response = httpx.get(
                    'https://127.0.0.1:{}/schwab-py-internal/status'.format(
                        callback_port),
                    verify=False,
                    timeout=min(_CALLBACK_SERVER_PROBE_TIMEOUT, remaining))
        except (httpx.ConnectError, httpx.TimeoutException):
            time.sleep(_CALLBACK_SERVER_POLL_INTERVAL)
            continue

        if (response.status_code != 200
                or not secrets.compare_digest(
                    response.text, readiness_token)):
            raise RedirectServerIdentityError(
                    'An unexpected HTTPS server answered on callback port {}. '
                    'Choose an unused callback port and try again.'.format(
                        callback_port))

        return

# Capture the real time.time so that we can use it in server initialization 
# while simultaneously mocking it in testing
__TIME_TIME = time.time

def client_from_login_flow(api_key, app_secret, callback_url, token_path,
                           asyncio=False, enforce_enums=True,
                           token_write_func=None, callback_timeout=300.0,
                           interactive=True, requested_browser=None):
    '''
    Open a web browser to perform an OAuth webapp login flow and creates a 
    client wrapped around the resulting token. The client will be configured to 
    refresh the token as necessary, writing each updated version to 
    ``token_path``.

    .. _callback_url_advisory:

    **Important Note:** This method operates by starting an HTTP server on the 
    port specified in your callback URL. When you complete the Schwab login 
    flow, Schwab sends a request to the callback URL with the required login 
    data encoded in the request parameters. *Anyone who receives this request 
    can steal your token and act on your account as though they were you.*

    ``schwab-py`` takes your security seriously. As a result, we only allow
    ``127.0.0.1`` as a host. We *strongly* recommend using a port number higher 
    than ``1024``, as most operating systems require superuser privileges to listen 
    on ports below ``1024``, and some also require changes to system firewalls 
    to accept connections to those ports, even when the connections originate from
    the same machine. The vast majority of users should just use
    ``https://127.0.0.1:8182`` as a callback URL.

    Note in particular that specifying *no* port number is equivalent to 
    specifying port 443, which is the default port number for HTTPS. Your 
    operating system will likely refuse to open this port for you, and this 
    method will fail.

    If you want to use this method but haven't specified a compatible callback 
    URL, you must update your app's configuration on `Schwab's developer portal 
    <https://developer.schwab.com/>`__. Note making this change will likely 
    require app re-approval from Schwab, which typically takes a few days.

    :param api_key: Your Schwab application's app key.
    :param app_secret: Application secret provided upon :ref:`app approval 
                       <approved_pending>`.
    :param callback_url: Your Schwab application's callback URL. Note this must
                         *exactly* match the value you've entered in your
                         application configuration, otherwise login will fail
                         with a security error. Be sure to check case and 
                         trailing slashes. :ref:`See the above note for
                         important information about setting your callback URL.
                         <callback_url_advisory>`
    :param token_path: Path to which the new token will be written. If the token
                       file already exists, it will be overwritten with a new
                       one. Updated tokens will be written to this path as well.
    :param asyncio: If set to ``True``, this will enable async support allowing
                    the client to be used in an async environment. Defaults to
                    ``False``
    :param enforce_enums: Set it to ``False`` to disable the enum checks on ALL
                          the client methods. Only do it if you know you really
                          need it. For most users, it is advised to use enums
                          to avoid errors.
    :param token_write_func: Function that writes the token on update. Will be
                             called whenever the token is updated, such as when
                             it is refreshed. See the above-mentioned example 
                             for what parameters this method takes. Login
                             requires a synchronous writer even when the
                             resulting client is asynchronous.
    :param callback_timeout: How long to wait for a callback from the server 
                             before giving up, in seconds. Wait forever if set
                             to zero or ``None``.
    :param interactive: Require user input before starting the browser.
    :param requested_browser: Name of the browser to attempt to open. This 
                              function uses the standard ``webbrowser`` library 
                              under the hood, so you can find a table of valid 
                              values
                              `here <https://docs.python.org/3/library/webbrowser.html#webbrowser.register>`__
    '''

    if callback_timeout is None:
        callback_timeout = 0
    if callback_timeout < 0:
        raise ValueError('callback_timeout must be positive')

    # Start the server
    parsed = urllib.parse.urlsplit(callback_url)

    if (parsed.scheme != 'https' or parsed.username is not None
            or parsed.password is not None or parsed.fragment):
        raise ValueError(
            'callback URL must use HTTPS without credentials or a fragment')

    if parsed.hostname != '127.0.0.1':
        # TODO: document this error
        raise ValueError(
                ('Disallowed hostname {}. client_from_login_flow only allows '+
                 'callback URLs with hostname 127.0.0.1. See here for ' +
                 'more information: https://schwab-py.readthedocs.io/en/' +
                 'latest/auth.html#callback-url-advisory').format(
                     parsed.hostname))

    callback_port = parsed.port if parsed.port else 443
    callback_path = _get_callback_path(parsed.path)

    if token_write_func is None:
        _check_token_path_writable(token_path)

    # Create the authorization state first so the callback server can ignore
    # requests that do not carry it.
    auth_context = get_auth_context(api_key, callback_url)

    # Forking a multithreaded process is unsafe on macOS. Use a local
    # spawn context without changing the application's global start method.
    process_context = (multiprocess.get_context('spawn')
                       if sys.platform == 'darwin' else multiprocess)
    output_queue = process_context.Queue()
    readiness_token = secrets.token_urlsafe(32)

    server = process_context.Process(
            target=__run_client_from_login_flow_server,
            args=(output_queue, callback_port, callback_path,
                  readiness_token, auth_context.state))

    # Context manager to kill the server upon completion
    @contextlib.contextmanager
    def callback_server():
        server.start()

        try:
            yield
        finally:
            try:
                psutil.Process(server.pid).kill()
            except psutil.NoSuchProcess:
                pass
            server.join(timeout=1.0)

    with callback_server():
        _wait_for_callback_server(
                server, callback_port, readiness_token)

        # Open the browser
        print()
        print('***********************************************************************')
        print()
        print('This is the browser-assisted login and token creation flow for')
        print('schwab-py. This flow automatically opens the login page on your')
        print('browser, captures the resulting OAuth callback, and creates a token')
        print('using the result. The authorization URL is:')
        print()
        print('>>', auth_context.authorization_url)
        print()
        print('IMPORTANT: Your browser will give you a security warning about an')
        print('invalid certificate prior to issuing the redirect. This is because')
        print('schwab-py has started a server on your machine to receive the OAuth')
        print('redirect using a self-signed SSL certificate. You can ignore that')
        print('warning, but make sure to first check that the URL matches your')
        print('callback URL, ignoring URL parameters. As a reminder, your callback URL')
        print('is:')
        print()
        print('>>',callback_url)
        print()
        print('See here to learn more about self-signed SSL certificates:')
        print('https://schwab-py.readthedocs.io/en/latest/auth.html#ssl-errors')
        print()
        print('If you encounter any issues, see here for troubleshooting:')
        print('https://schwab-py.readthedocs.io/en/latest/auth.html#troubleshooting')
        print('***********************************************************************')
        print()

        if interactive:
            input('Press ENTER to open the browser. Note you can call ' +
                  'this method with interactive=False to skip this input.')

        controller = webbrowser.get(requested_browser)
        controller.open(auth_context.authorization_url)

        # Wait for a response. A monotonic clock keeps system clock
        # adjustments from shortening or extending the wait.
        timeout_time = time.monotonic() + callback_timeout
        received_url = None
        while True:
            now = time.monotonic()
            if now >= timeout_time:
                if callback_timeout == 0:
                    # XXX: We're detecting a test environment here to avoid an 
                    #      infinite sleep. Surely there must be a better way to do 
                    #      this...
                    if __TIME_TIME != time.time:  # pragma: no cover
                        raise ValueError('endless wait requested')
                else:
                    break

            # Attempt to fetch from the queue
            try:
                queue_timeout = (
                    0.1 if callback_timeout == 0
                    else max(0, min(timeout_time - now, 0.1)))
                received_url = output_queue.get(
                        timeout=queue_timeout)
                break
            except queue.Empty:
                if server.exitcode is not None:
                    raise RedirectServerExitedError(
                        'Redirect server exited while waiting for the callback '
                        'on port {} (exit code {}).'.format(
                            callback_port, server.exitcode))

        if not received_url:
            raise RedirectTimeoutError(
                    'Timed out waiting for a post-authorization callback. You '+
                    'can set a longer timeout by passing a value of ' +
                    'callback_timeout to client_from_login_flow.')

        token_write_func = (
            __make_update_token_func(token_path) if token_write_func is None
            else token_write_func)

        return client_from_received_url(
                api_key, app_secret, auth_context, received_url, 
                token_write_func, asyncio, enforce_enums)


################################################################################
# client_from_token_path


def client_from_token_file(token_path: str | os.PathLike[str], api_key: str,
                           app_secret: str, asyncio: bool = False,
                           enforce_enums: bool = True) -> Client | AsyncClient:
    '''
    Returns a session from an existing token file. The session will perform
    an auth refresh as needed. It will also update the token on disk whenever
    appropriate.

    Use one client owner per token. Independent clients or processes sharing a
    token file do not coordinate refreshes and can overwrite newer tokens.

    :param token_path: Path to an existing token. Updated tokens will be written
                       to this path. If you do not yet have a token, use
                       :func:`~schwab.auth.client_from_login_flow` or
                       :func:`~schwab.auth.easy_client` to create one.
    :param api_key: Your Schwab application's app key.
    :param app_secret: Application secret. Provided upon :ref:`app approval 
                       <approved_pending>`.
    :param asyncio: If set to ``True``, this will enable async support allowing
                    the client to be used in an async environment. Defaults to
                    ``False``
    :param enforce_enums: Set it to ``False`` to disable the enum checks on ALL
                          the client methods. Only do it if you know you really
                          need it. For most users, it is advised to use enums
                          to avoid errors.
    '''

    load = __token_loader(token_path)

    return client_from_access_functions(
        api_key, app_secret, load, __make_update_token_func(token_path),
        asyncio=asyncio, enforce_enums=enforce_enums)


################################################################################
# client_from_manual_flow


def client_from_manual_flow(api_key, app_secret, callback_url, token_path,
                            asyncio=False, token_write_func=None,
                            enforce_enums=True):
    '''
    Walks the user through performing an OAuth login flow by manually
    copy-pasting URLs, and returns a client wrapped around the resulting token.
    The client will be configured to refresh the token as necessary, writing
    each updated version to ``token_path``.

    :param api_key: Your Schwab application's app key.
    :param app_secret: Application secret provided upon :ref:`app approval 
                       <approved_pending>`.
    :param callback_url: Your Schwab application's callback URL. Note this must
                         *exactly* match the value you've entered in your
                         application configuration, otherwise login will fail
                         with a security error. Be sure to check case and 
                         trailing slashes.
    :param token_path: Path to which the new token will be written. If the token
                       file already exists, it will be overwritten with a new
                       one. Updated tokens will be written to this path as well.
    :param asyncio: If set to ``True``, this will enable async support allowing
                    the client to be used in an async environment. Defaults to
                    ``False``
    :param enforce_enums: Set it to ``False`` to disable the enum checks on ALL
                          the client methods. Only do it if you know you really
                          need it. For most users, it is advised to use enums
                          to avoid errors.
    '''
    get_logger().info('Creating new token with callback URL \'%s\' ' +
                       'and token path \'%s\'', callback_url, token_path)

    if token_write_func is None:
        _check_token_path_writable(token_path)

    auth_context = get_auth_context(api_key, callback_url)

    print('\n**************************************************************\n')
    print('This is the manual login and token creation flow for schwab-py.')
    print('Please follow these instructions exactly:')
    print()
    print(' 1. Open the following link by copy-pasting it into the browser')
    print('    of your choice:')
    print()
    print('        ' + auth_context.authorization_url)
    print()
    print(' 2. Log in with your account credentials. You may be asked to')
    print('    perform two-factor authentication using text messaging or')
    print('    another method, as well as whether to trust the browser.')
    print()
    print(' 3. When asked whether to allow your app access to your account,')
    print('    select "Allow".')
    print()
    print(' 4. Your browser should be redirected to your callback URI. Copy')
    print('    the ENTIRE address, paste it into the following prompt, and press')
    print('    Enter/Return.')
    print()
    print('If you encounter any issues, see here for troubleshooting:')
    print('https://schwab-py.readthedocs.io/en/latest/auth.html#troubleshooting')
    print('\n**************************************************************\n')

    if callback_url.startswith('http://'):
        print(('WARNING: Your callback URL ({}) will transmit data over HTTP, ' +
               'which is a potentially severe security vulnerability. ' +
               'Please go to your app\'s configuration with Schwab ' +
               'and update your callback URL to begin with \'https\' ' +
               'to stop seeing this message.').format(callback_url))

    received_url = input('Redirect URL> ').strip()

    token_write_func = (
        __make_update_token_func(token_path) if token_write_func is None
        else token_write_func)

    return client_from_received_url(
            api_key, app_secret, auth_context, received_url, token_write_func, 
            asyncio, enforce_enums)


################################################################################
# client_from_access_functions


def client_from_access_functions(
        api_key: str, app_secret: str,
        token_read_func: Callable[[], dict[str, Any]],
        token_write_func: Callable[..., Any], asyncio: bool = False,
        enforce_enums: bool = True, *,
        offload_sync_token_writer: bool = False) -> Client | AsyncClient:
    '''
    Returns a session from an existing token file, using the accessor methods to
    read and write the token. This is an advanced method for users who do not
    have access to a standard writable filesystem, such as users of AWS Lambda
    and other serverless products who must persist token updates on
    non-filesystem places, such as S3. 99.9% of users should not use this
    function.

    Users are free to customize how they represent the token file. In theory,
    since they have direct access to the token, they can get creative about how
    they store it and fetch it. In practice, it is *highly* recommended to
    simply accept the token object and use ``json`` to serialize and
    deserialize it, without inspecting it in any way.

    Independent clients do not coordinate refreshes against a shared token
    store. Use a single owner for each token or provide external coordination
    for the entire refresh and persistence operation.

    Note the read and write methods must take particular arguments. Please see 
    `this example <https://github.com/alexgolec/schwab-py/tree/master/examples/
    client_from_access_functions.py>`__ for details.

    :param api_key: Your Schwab application's app key.
    :param app_secret: Application secret. Provided upon :ref:`app approval 
                       <approved_pending>`.
    :param token_read_func: Function that takes no arguments and returns a token
                            object.
    :param token_write_func: Function that writes the token on update. Will be
                             called whenever the token is updated, such as when
                             it is refreshed. See the above-mentioned example 
                             for what parameters this method takes. Async
                             writers are supported when ``asyncio=True``.
    :param asyncio: If set to ``True``, this will enable async support allowing
                    the client to be used in an async environment. Defaults to
                    ``False``
    :param enforce_enums: Set it to ``False`` to disable the enum checks on ALL
                          the client methods. Only do it if you know you really
                          need it. For most users, it is advised to use enums
                          to avoid errors.
    :param offload_sync_token_writer: Run the writer in a worker thread for an
                          async client. Enable only for thread-safe writers.
                          Defaults to ``False``.
    '''
    if type(offload_sync_token_writer) is not bool:
        raise ValueError('offload_sync_token_writer must be a bool')
    token = token_read_func()

    # Extract metadata and unpack the token, if necessary
    metadata = TokenMetadata.from_loaded_token(token, token_write_func)
    metadata.offload_sync_token_writer = offload_sync_token_writer
    return _client_from_token_metadata(
        api_key, app_secret, metadata, asyncio, enforce_enums)


################################################################################
# Tools for incorporating token generation into webapp workflows


AuthContext = collections.namedtuple(
        'AuthContext', ['callback_url', 'authorization_url', 'state'])

def get_auth_context(api_key, callback_url, state=None):
    oauth = OAuth2Client(api_key, redirect_uri=callback_url)
    try:
        authorization_url, state = oauth.create_authorization_url(
            'https://api.schwabapi.com/v1/oauth/authorize',
            state=state)
    finally:
        oauth.close()

    return AuthContext(callback_url, authorization_url, state)


def client_from_received_url(
        api_key, app_secret, auth_context, received_url, token_write_func, 
        asyncio=False, enforce_enums=True):
    # XXX: The AuthContext must be serializable, which means the original 
    #      OAuth2Client created in get_auth_context cannot be passed around. 
    #      Instead, we reconstruct it here.
    if inspect.iscoroutinefunction(token_write_func) or inspect.iscoroutinefunction(
            getattr(token_write_func, '__call__', None)):
        raise TypeError('login requires a synchronous token writer; use '
                        'client_from_access_functions for async writers')
    oauth = OAuth2Client(api_key, redirect_uri=auth_context.callback_url)
    try:
        token = oauth.fetch_token(
            TOKEN_ENDPOINT,
            authorization_response=received_url,
            client_id=api_key, auth=(api_key, app_secret),
            state=auth_context.state)
    finally:
        oauth.close()

    # Don't emit token details in debug logs
    register_redactions(token)

    # Set up token writing and perform the initial token write
    metadata_manager = TokenMetadata(token, int(time.time()), token_write_func)
    token_write_func = metadata_manager.wrapped_token_write_func()
    token_write_func(token)

    return _client_from_token_metadata(
        api_key, app_secret, metadata_manager, asyncio, enforce_enums)


################################################################################
# easy_client


# TODO: Figure out how to properly mock global objects in unittest. This hack 
# ensures that the _get_ipython variable is defined so that we can patch is 
# using module-level patching. This is safe in most contexts, but there are 
# circumstances where it gets weird like starting an ipython notebook after 
# schwab-py is loaded.
try:
    _get_ipython = get_ipython
except NameError:
    _get_ipython = None


def __running_in_notebook():
    # Google Colab
    if os.getenv('COLAB_RELEASE_TAG'):
        return True

    # ipython in notebook mode
    if _get_ipython is not None:
        shell = _get_ipython().__class__.__name__
        if shell == 'ZMQInteractiveShell':
            return True

    return False


def easy_client(api_key, app_secret, callback_url, token_path, asyncio=False, 
                enforce_enums=True, max_token_age=60*60*24*6.5,
                callback_timeout=300.0, interactive=True,
                requested_browser=None):
    '''
    Convenient wrapper around :func:`client_from_login_flow` and
    :func:`client_from_token_file`. If ``token_path`` exists, loads the token
    from it. Otherwise open a login flow to fetch a new token. Returns a client
    configured to refresh the token to ``token_path``.

    *Reminder:* You should never create the token file yourself or modify it in
    any way. If ``token_path`` refers to an existing file, this method will
    assume that file is valid token and will attempt to parse it.

    :param api_key: Your Schwab application's app key.
    :param app_secret: Application secret provided upon :ref:`app approval 
                       <approved_pending>`.
    :param callback_url: Your Schwab application's callback URL. Note this must
                         *exactly* match the value you've entered in your
                         application configuration, otherwise login will fail
                         with a security error. Be sure to check case and 
                         trailing slashes. :ref:`See the above note for
                         important information about setting your callback URL.
                         <callback_url_advisory>`
    :param token_path: Path to which the new token will be written. If the token
                       file already exists, it will be overwritten with a new
                       one. Updated tokens will be written to this path as well.
    :param asyncio: If set to ``True``, this will enable async support allowing
                    the client to be used in an async environment. Defaults to
                    ``False``
    :param enforce_enums: Set it to ``False`` to disable the enum checks on ALL
                          the client methods. Only do it if you know you really
                          need it. For most users, it is advised to use enums
                          to avoid errors.
    :param max_token_age: If the token is loaded from a file but is older than 
                          this age (in seconds), proactively delete it and 
                          create a new one. Assists with 
                          :ref:`token expiration <token_expiration>`. If set to 
                          None, never proactively delete the token.
    :param callback_timeout: See the corresponding parameter to 
                             :func:`client_from_login_flow 
                             <client_from_login_flow>`.
    :param interactive: See the corresponding parameter to 
                        :func:`client_from_login_flow 
                        <client_from_login_flow>`.
    :param requested_browser: See the corresponding parameter to 
                              :func:`client_from_login_flow 
                              <client_from_login_flow>`.
    '''
    if max_token_age is None:
        max_token_age = 0
    if max_token_age < 0:
        raise ValueError('max_token_age must be positive, zero, or None')

    logger = get_logger()

    c = None

    if os.path.isfile(token_path):
        c = client_from_token_file(token_path, api_key, app_secret,
                                   asyncio=asyncio,
                                   enforce_enums=enforce_enums)
        logger.info('Loaded token from file \'%s\'', token_path)

        if max_token_age > 0 and c.token_age() >= max_token_age:
            logger.info('token too old, proactively creating a new one')
            if not asyncio:
                # Release the unused client's connection pool. An async
                # session has made no requests yet and cannot be awaited
                # from this synchronous function.
                c.session.close()
            c = None

    # Return early on success
    if c is not None:
        return c

    # Detect whether we're running in a notebook
    if __running_in_notebook():
        c = client_from_manual_flow(api_key, app_secret, callback_url, 
                                    token_path, asyncio=asyncio,
                                    enforce_enums=enforce_enums)
        logger.info(
            'Returning client fetched using manual flow, writing ' +
            'token to \'%s\'', token_path)
    else:
        c = client_from_login_flow(
            api_key, app_secret, callback_url, token_path, asyncio=asyncio,
            enforce_enums=enforce_enums, callback_timeout=callback_timeout,
            requested_browser=requested_browser, interactive=interactive)

        logger.info(
            'Returning client fetched using web browser, writing ' +
            'token to \'%s\'', token_path)

    return c
