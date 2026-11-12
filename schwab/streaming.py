from abc import ABC, abstractmethod
from collections import defaultdict, deque
from enum import Enum

import asyncio
import copy
import httpx2 as httpx
import inspect
import json
import math
import warnings

from websockets.asyncio import client as ws_client

from .utils import EnumEnforcer, LazyLog
from .debug import get_redacted_logger, get_websocket_logger, redact_json
from .client.synchronous import Client


class StreamJsonDecoder(ABC):
    @abstractmethod
    def decode_json_string(self, raw):
        '''
        Parse a JSON-formatted string into a proper object. Any exception
        raised here is reported to consumers as :class:`UnparsableMessage`.
        '''
        raise NotImplementedError()


class NaiveJsonStreamDecoder(StreamJsonDecoder):
    def decode_json_string(self, raw):
        return json.loads(raw)


def get_logger():
    return get_redacted_logger(__name__)


_JSON_ATOMIC_TYPES = frozenset((str, int, float, bool, type(None)))


def _json_clone(obj):
    '''Deep-copy a decoded JSON tree.

    Exact ``dict`` and ``list`` instances are rebuilt directly; everything else
    (dict or list subclasses, tuples, ``Decimal``, custom objects) is copied
    with :func:`copy.deepcopy`, so values from custom decoders keep its
    semantics. Unlike ``deepcopy``, objects shared within one message are copied
    separately; ``json.loads`` never shares objects.
    '''
    cls = type(obj)
    if cls is dict:
        return {key: value if type(value) in _JSON_ATOMIC_TYPES
                else _json_clone(value)
                for key, value in obj.items()}
    if cls is list:
        return [value if type(value) in _JSON_ATOMIC_TYPES
                else _json_clone(value)
                for value in obj]
    if cls in _JSON_ATOMIC_TYPES:
        return obj
    return copy.deepcopy(obj)


def _copy_message(message):
    '''Independent deep copy of a message for one handler.'''
    try:
        return _json_clone(message)
    except RecursionError:
        # Cyclic structures can only come from custom decoders; deepcopy
        # handles them.
        return copy.deepcopy(message)


class _BaseFieldEnum(Enum):
    @classmethod
    def all_fields(cls):
        return list(cls)

    @classmethod
    def key_mapping(cls):
        # Read the cache from this class only. An inherited value would be
        # the mapping of a different enum.
        mapping = cls.__dict__.get('_key_mapping')
        if mapping is None:
            mapping = {str(enum.value): enum.name for enum in cls}
            cls._key_mapping = mapping
        return mapping

    @classmethod
    def relabel_message(cls, old_msg, new_msg):
        # Make a copy of the items so we can modify the dict during iteration
        key_mapping = cls.key_mapping()
        for old_key in list(old_msg):
            if old_key in key_mapping:
                new_key = key_mapping[old_key]
                new_msg[new_key] = new_msg.pop(old_key)

    @classmethod
    def relabeled_copy(cls, entry):
        '''Deep copy of a plain ``dict`` entry with numeric keys renamed.

        Produces the same keys, values and key order as deep-copying and then
        calling :meth:`relabel_message`: unmapped keys keep their order and are
        followed by renamed keys in their original order.
        '''
        key_mapping = cls.key_mapping()
        unmapped = {}
        mapped = {}
        for key, value in entry.items():
            if type(value) not in _JSON_ATOMIC_TYPES:
                value = _json_clone(value)
            new_key = key_mapping.get(key)
            if new_key is None:
                unmapped[key] = value
            else:
                mapped[new_key] = value
        unmapped.update(mapped)
        return unmapped


class UnexpectedResponse(Exception):
    def __init__(self, response, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.response = response


class UnexpectedResponseCode(Exception):
    def __init__(self, response, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.response = response


class StreamClosedError(ConnectionError):
    '''The stream was closed or replaced while an operation was pending.'''


class StreamBufferOverflowError(StreamClosedError):
    '''The stream closed because its pending message buffer reached capacity.'''


class UnparsableMessage(Exception):
    def __init__(self, raw_msg, json_parse_exception, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.raw_msg = raw_msg
        self.json_parse_exception = json_parse_exception


class _Handler:
    def __init__(self, func, field_enum_type):
        self._func = func
        self._field_enum_type = field_enum_type

    def __call__(self, *args, **kwargs):
        return self._func(*args, **kwargs)

    def label_message(self, msg):
        '''Return a relabeled deep copy, leaving ``msg`` untouched so that a
        cancelled dispatch can resume from the original message.'''
        if 'content' not in msg:
            return msg
        if type(msg) is dict and type(msg['content']) is list:
            try:
                return self._relabeled_json_copy(msg)
            except RecursionError:
                pass
        # Custom container types, unusual content shapes and cyclic messages
        # keep the original deepcopy-then-relabel behaviour.
        new_msg = copy.deepcopy(msg)
        for idx in range(len(msg['content'])):
            self._field_enum_type.relabel_message(msg['content'][idx],
                                                  new_msg['content'][idx])
        return new_msg

    def _relabeled_json_copy(self, msg):
        # Copy and relabel in one pass instead of deep-copying first.
        field_enum = self._field_enum_type
        new_msg = {}
        for key, value in msg.items():
            if key == 'content':
                new_content = []
                for entry in value:
                    if type(entry) is dict:
                        new_content.append(field_enum.relabeled_copy(entry))
                    else:
                        entry_copy = _json_clone(entry)
                        field_enum.relabel_message(entry, entry_copy)
                        new_content.append(entry_copy)
                new_msg[key] = new_content
            elif type(value) in _JSON_ATOMIC_TYPES:
                new_msg[key] = value
            else:
                new_msg[key] = _json_clone(value)
        return new_msg


class _PendingMessage(dict):
    '''A received message whose handler dispatch can resume after cancellation.'''
    def __init__(self, message):
        super().__init__(message)
        self.events = ([(entry, True) for entry in message.get('data', [])]
                       + [(entry, False) for entry in message.get('notify', [])])
        self.next_event = 0
        self.next_handler = 0
        self.handlers = None


class StreamClient(EnumEnforcer):

    def __init__(self, client, *, account_id=None,
                 enforce_enums=True, ssl_context=None, response_timeout=30.0,
                 max_pending_handler_tasks=100, max_pending_messages=1000,
                 offload_sync_preferences=False, on_handler_error=None,
                 sequential_handler_services=(), copy_messages=True,
                 max_symbols_per_request=500):
        super().__init__(enforce_enums)

        if type(copy_messages) is not bool:
            raise ValueError('copy_messages must be a bool')
        self._copy_messages = copy_messages
        if type(max_symbols_per_request) is not int or max_symbols_per_request <= 0:
            raise ValueError('max_symbols_per_request must be a positive int')
        self._max_symbols_per_request = max_symbols_per_request

        if on_handler_error is not None and (
                not callable(on_handler_error)
                or inspect.iscoroutinefunction(on_handler_error)
                or inspect.iscoroutinefunction(
                    getattr(on_handler_error, '__call__', None))):
            raise ValueError('on_handler_error must be a synchronous callable or None')
        self._on_handler_error = on_handler_error
        if isinstance(sequential_handler_services, str):
            raise ValueError('sequential_handler_services must be an iterable of service names')
        self._sequential_handler_services = frozenset(sequential_handler_services)
        if not all(isinstance(service, str) for service in self._sequential_handler_services):
            raise ValueError('sequential_handler_services must contain strings')
        self._service_handler_locks = {}

        if type(offload_sync_preferences) is not bool:
            raise ValueError('offload_sync_preferences must be a bool')
        self._offload_sync_preferences = offload_sync_preferences

        if type(max_pending_messages) is not int or max_pending_messages <= 0:
            raise ValueError('max_pending_messages must be a positive int')
        self._max_pending_messages = max_pending_messages

        if (type(max_pending_handler_tasks) is not int
                or max_pending_handler_tasks <= 0):
            raise ValueError('max_pending_handler_tasks must be a positive int')

        if response_timeout is not None and (
                type(response_timeout) not in (int, float)
                or not math.isfinite(response_timeout)
                or response_timeout <= 0):
            raise ValueError(
                'response_timeout must be None or a positive, finite number '
                'of seconds')

        if account_id is not None:
            warnings.warn(
                'account_id is deprecated and has no effect; streamer access '
                'is determined by Schwab user preferences. Remove this '
                'argument.',
                DeprecationWarning,
                stacklevel=2)

        self._ssl_context = ssl_context
        self._client = client

        # Set by the login() function
        self._stream_correl_id = None
        self._stream_customer_id = None
        self._stream_channel = None
        self._stream_function_id = None
        self._socket = None

        # Internal fields
        self._request_id = 0
        self._last_subscription_fields = {}
        self._subscriptions = {}
        self._subscription_locks = defaultdict(asyncio.Lock)
        self._handlers = defaultdict(list)
        self._handler_tasks = set()
        self._max_pending_handler_tasks = max_pending_handler_tasks
        self._handler_slots = asyncio.Semaphore(max_pending_handler_tasks)
        self._event_loop = None
        self._connection_generation = 0
        self._response_timeout = response_timeout

        # When listening for responses, we sometimes encounter non-response
        # messages. Since this happens outside the context of the handler
        # dispatcher, we cannot handle these messages. However, we still need to
        # deliver these messages. This list records the messages that were read
        # from the stream but not handled yet. Messages should be read from this
        # list before they are read from the stream.
        self._overflow_items = deque()

        # Logging-related fields
        self.logger = get_logger()
        self.request_number = 0

        # Initialize the JSON parser to be the naive parser which directly calls
        # ``json.loads``
        self.json_decoder = NaiveJsonStreamDecoder()
        self._send_lock = asyncio.Lock()
        self._dispatch_lock = asyncio.Lock()
        self._message_waiters = deque()
        self._response_waiters = {}
        self._reader_task = None
        self._reader_wakeup = asyncio.Event()

    def set_json_decoder(self, json_decoder):
        '''
        Sets a custom JSON decoder.

        :param json_decoder: Custom JSON decoder to use for to decode all
                             incoming JSON strings. See
                             :class:`StreamJsonDecoder` for details.
        '''
        if not isinstance(json_decoder, StreamJsonDecoder):
            raise ValueError('Custom JSON parser must be a subclass of ' +
                             'schwab.streaming.StreamJsonDecoder')
        self.json_decoder = json_decoder

    def req_num(self):
        self.request_number += 1
        return self.request_number

    async def _send(self, obj):
        if self._socket is None:
            raise ValueError(
                'Socket not open. Did you forget to call login()?')

        self.logger.debug('Send %s: Sending %s',
                self.req_num(), LazyLog(lambda: redact_json(obj, stream=True)))

        await self._socket.send(json.dumps(obj))

    async def _receive(self):
        '''Read and decode one frame from the socket. Buffered messages are
        delivered by the reader loop, not here.'''
        if self._socket is None:
            raise ValueError(
                'Socket not open. Did you forget to call login()?')

        raw = await self._socket.recv()
        try:
            ret = self.json_decoder.decode_json_string(raw)
        except Exception as e:
            # Custom decoders may raise any exception type. Report every
            # decoding failure the same way so the reader keeps running.
            msg = ('Failed to parse stream message. The raw message is '
                   'available in raw_msg and may contain sensitive data.')
            raise UnparsableMessage(raw, e, msg) from e

        error = self._envelope_error(ret)
        if error is not None:
            cause = ValueError(error)
            raise UnparsableMessage(
                raw, cause, 'Invalid stream message envelope') from cause

        self.logger.debug(
            'Receive %s: Returning message from stream: %s',
            self.req_num(), LazyLog(lambda: redact_json(ret, stream=True)))

        return ret

    @staticmethod
    def _envelope_error(message):
        if not isinstance(message, dict):
            return 'Stream message must be an object'
        for key in ('data', 'notify'):
            if key in message and not isinstance(message[key], list):
                return '{} must be a list'.format(key)
        if 'response' not in message:
            return None
        responses = message['response']
        if (not isinstance(responses, list) or not responses
                or not isinstance(responses[0], dict)):
            return 'Response must be a nonempty list of objects'
        response = responses[0]
        try:
            int(response['requestid'])
        except (KeyError, ValueError, TypeError, OverflowError):
            return 'Response request ID is missing or invalid'
        content = response.get('content')
        if ('service' not in response or 'command' not in response
                or not isinstance(content, dict) or 'code' not in content
                or (content['code'] != 0 and 'msg' not in content)):
            return 'Response fields are missing or invalid'
        return None

    async def _init_from_preferences(self, prefs, websocket_connect_args):
        await self._close_connection()
        websocket_connect_args = dict(websocket_connect_args)
        websocket_connect_args.setdefault('logger', get_websocket_logger())

        # Record streamer subscription keys
        stream_info = prefs['streamerInfo'][0]

        self._stream_correl_id = stream_info['schwabClientCorrelId']
        self._stream_customer_id = stream_info['schwabClientCustomerId']
        self._stream_channel = stream_info['schwabClientChannel']
        self._stream_function_id = stream_info['schwabClientFunctionId']

        # Initialize socket
        wss_url = stream_info['streamerSocketUrl']

        if self._ssl_context:
            websocket_connect_args['ssl'] = self._ssl_context

        self._socket = await ws_client.connect(
                wss_url, **websocket_connect_args)


    def _make_request(self, *, service, command, parameters):
        request_id = self._request_id
        self._request_id += 1

        request = {
            'service': service,
            'requestid': str(request_id),
            'command': command,
            'SchwabClientCustomerId': self._stream_customer_id,
            'SchwabClientCorrelId': self._stream_correl_id,
            'parameters': parameters,
        }

        return request, request_id

    def _validate_response(self, resp, request_id, service, command):
        if 'response' not in resp:
            raise UnexpectedResponse(resp, 'response payload missing')

        response = resp['response'][0]

        resp_request_id = int(response['requestid'])
        if resp_request_id != request_id:
            raise UnexpectedResponse(
                resp, 'unexpected requestid: {}'.format(resp_request_id))

        resp_service = response['service']
        if resp_service != service:
            raise UnexpectedResponse(
                resp, 'unexpected service: {}'.format(resp_service))

        resp_command = response['command']
        if resp_command != command:
            raise UnexpectedResponse(
                resp, 'unexpected command: {}'.format(resp_command))

        resp_code = response['content']['code']
        if resp_code != 0:
            raise UnexpectedResponseCode(
                resp,
                'unexpected response code: {}, msg is \'{}\''.format(
                    resp_code, response['content']['msg']))

    def _ensure_reader(self):
        if self._reader_task is None or self._reader_task.done():
            self._reader_task = asyncio.create_task(self._reader_loop())
        self._reader_wakeup.set()

    def _next_message_waiter(self):
        while self._message_waiters:
            waiter = self._message_waiters.popleft()
            if not waiter.done():
                return waiter
        return None

    def _has_message_waiters(self):
        while (self._message_waiters
                and self._message_waiters[0].done()):
            self._message_waiters.popleft()
        return bool(self._message_waiters)

    def _deliver_message(self, msg):
        waiter = self._next_message_waiter()
        if waiter is None:
            return False
        waiter.set_result(msg)
        return True

    async def _stop_reader(self):
        task = self._reader_task
        if (task is not None and not task.done()
                and task is not asyncio.current_task()):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self._reader_wakeup.clear()

        for waiter in self._response_waiters.values():
            if not waiter.done():
                waiter.set_exception(StreamClosedError('Stream connection closed'))
        for waiter in self._message_waiters:
            if not waiter.done():
                waiter.set_exception(StreamClosedError('Stream connection closed'))
        self._response_waiters.clear()
        self._message_waiters.clear()

    async def _stop_reader_if_idle(self):
        task = self._reader_task
        if (not self._response_waiters and not self._has_message_waiters()
                and task is not None and not task.done()
                and task is not asyncio.current_task()):
            # Cancelling a reader blocked in recv() must not lose or reorder
            # frames. websockets 17.3 fixed a fragmented-message cancellation
            # race; setup.py also requires the security fix in 17.4.
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

            # A waiter may have arrived while the cancelled reader task was
            # finishing. Ensure it is not left without a dispatcher.
            if self._response_waiters or self._has_message_waiters():
                self._ensure_reader()

    async def _stop_handler_tasks(self):
        # A handler may itself initiate logout or reconnect.
        tasks = self._handler_tasks - {asyncio.current_task()}
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _close_connection(self):
        self._connection_generation += 1
        socket, self._socket = self._socket, None
        try:
            await self._stop_reader()
        finally:
            self._overflow_items.clear()
            try:
                await self._stop_handler_tasks()
            finally:
                if socket is not None:
                    await socket.close()

    def _buffer_message(self, message):
        if len(self._overflow_items) >= self._max_pending_messages:
            raise StreamBufferOverflowError('Pending stream message buffer is full')
        self._overflow_items.append(message)

    async def _reader_loop(self):
        try:
            while True:
                await self._reader_wakeup.wait()

                while True:
                    while self._overflow_items:
                        if not self._deliver_message(self._overflow_items[0]):
                            break
                        self._overflow_items.popleft()

                    if (not self._response_waiters
                            and not self._has_message_waiters()):
                        self._reader_wakeup.clear()
                        break

                    # Read acknowledgements even when data is queued. A
                    # cancelled consumer may restore older data during recv.
                    try:
                        msg = await self._receive()
                    except UnparsableMessage as exc:
                        # Preserve FIFO delivery without failing acknowledgement
                        # waiters. The next message consumer raises this marker.
                        self._buffer_message(exc)
                        continue

                    if 'response' in msg:
                        response_request_id = int(
                                msg['response'][0]['requestid'])
                        waiter = self._response_waiters.pop(
                                response_request_id, None)

                        if waiter is not None and not waiter.done():
                            waiter.set_result(msg)
                        elif response_request_id < self._request_id:
                            # This request was issued, but its waiter has
                            # already completed, timed out, or been cancelled.
                            # A late acknowledgement must not be mistaken for
                            # a stream message or a response to a newer request.
                            self.logger.warning(
                                    'Ignoring late response for request %s: '
                                    '%s',
                                    response_request_id,
                                    LazyLog(lambda msg=msg: redact_json(msg, stream=True)))
                        # Preserve the historical behavior for malformed or
                        # future response IDs: the sole request waiter receives
                        # the response and raises UnexpectedResponse during
                        # validation.
                        elif len(self._response_waiters) == 1:
                            _, waiter = self._response_waiters.popitem()
                            if not waiter.done():
                                waiter.set_result(msg)
                        else:
                            self._buffer_message(msg)
                    else:
                        self._buffer_message(msg)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            delivered = False
            for waiter in self._response_waiters.values():
                if not waiter.done():
                    waiter.set_exception(exc)
                    delivered = True
            for waiter in self._message_waiters:
                if not waiter.done():
                    waiter.set_exception(exc)
                    delivered = True
            self._response_waiters.clear()
            self._message_waiters.clear()
            if isinstance(exc, StreamBufferOverflowError):
                self.logger.error('Closing stream: pending message buffer is full')
                await self._close_connection()
            elif not delivered:
                self.logger.exception('Stream reader failed without an active waiter')
        finally:
            self._reader_wakeup.clear()
            self._reader_task = None

    async def _send_request_and_await_response(
            self, request, request_id, service, command, *,
            acquire_send_lock=True):
        loop = asyncio.get_running_loop()
        waiter = loop.create_future()
        self._response_waiters[request_id] = waiter

        try:
            if acquire_send_lock:
                async with self._send_lock:
                    await self._send({'requests': [request]})
            else:
                await self._send({'requests': [request]})
            self._ensure_reader()
            if self._response_timeout is None:
                resp = await waiter
            else:
                resp = await asyncio.wait_for(
                        waiter, timeout=self._response_timeout)
        except BaseException:
            self._response_waiters.pop(request_id, None)
            await self._stop_reader_if_idle()
            raise
        else:
            self._response_waiters.pop(request_id, None)

        self._validate_response(resp, request_id, service, command)

    async def _service_op(self, symbols, service, command, field_type=None,
                          *, fields=None):
        async with self._subscription_locks[service]:
            await self._service_chunks_locked(
                symbols, service, command, field_type, fields=fields)

    async def _service_chunks_locked(self, symbols, service, command, field_type,
                                     *, fields=None, converted=False, record=True):
        if self._socket is None:
            raise ValueError('Socket not open. Did you forget to call login()?')
        if isinstance(symbols, str):
            symbols = [symbols]
        else:
            symbols = list(symbols)
        if any(not isinstance(s, str) for s in symbols):
            raise ValueError('symbols must be nonempty strings')
        symbols = [part for symbol in symbols for part in symbol.split(',')]
        if not symbols:
            raise ValueError('symbols must not be empty')
        if any(not s.strip() for s in symbols):
            raise ValueError('symbols must be nonempty strings')
        symbols = list(dict.fromkeys(symbols))
        if fields is not None and not isinstance(fields, (str, Enum)):
            fields = tuple(fields)
        for offset in range(0, len(symbols), self._max_symbols_per_request):
            chunk = symbols[offset:offset + self._max_symbols_per_request]
            chunk_command = 'ADD' if command == 'SUBS' and offset else command
            await self._service_op_locked(
                chunk, service, chunk_command, field_type, fields=fields,
                converted=converted, record=record)

    async def _service_op_locked(self, symbols, service, command, field_type=None,
                                 *, fields=None, converted=False, record=True):
        if isinstance(symbols, str):
            symbols = [symbols]
        else:
            symbols = list(symbols)

        if field_type is not None:
            previous_fields = False
            if fields is None:
                previous = self._last_subscription_fields.get(service)
                if command == 'ADD' and previous is not None:
                    fields = previous
                    previous_fields = True
                else:
                    fields = field_type.all_fields()

            if not previous_fields and not converted:
                fields = self.convert_enum_iterable(fields, field_type)
            if not fields:
                raise ValueError(
                    'fields must not be empty; pass None to request all fields')
            # Converted values are deduplicated so a required field that was
            # also passed as a raw value is not requested twice.
            fields = sorted(set(fields))

        # Check the connection before formatting keys: some keys, such as the
        # account activity key, only exist after login.
        if self._socket is None:
            raise ValueError(
                'Socket not open. Did you forget to call login()?')

        parameters = {
            'keys': ','.join(symbols)
        }
        if field_type is not None:
            parameters['fields'] = ','.join(str(f) for f in fields)

        request, request_id = self._make_request(
            service=service, command=command,
            parameters=parameters)

        await self._send_request_and_await_response(
                request, request_id, service, command)
        if record:
            if command in ('SUBS', 'ADD'):
                if command == 'SUBS' or service not in self._subscriptions:
                    self._subscriptions[service] = {}
                values = tuple(fields) if field_type is not None else None
                for symbol in symbols:
                    self._subscriptions[service][symbol] = (field_type, values)
                if command == 'SUBS' and field_type is not None:
                    self._last_subscription_fields[service] = values
            elif command == 'UNSUBS' and service in self._subscriptions:
                if service == 'ACCT_ACTIVITY':
                    self._subscriptions[service].clear()
                else:
                    for symbol in symbols:
                        self._subscriptions[service].pop(symbol, None)
                if not self._subscriptions[service]:
                    del self._subscriptions[service]
                    self._last_subscription_fields.pop(service, None)

    async def resubscribe(self):
        '''Restore acknowledged subscriptions after a successful login.

        Failures leave the registry intact so the caller can retry. A request
        that timed out before its acknowledgement may need reconciliation.
        '''
        for service in list(self._subscriptions):
            async with self._subscription_locks[service]:
                entries = self._subscriptions.get(service, {})
                groups = {}
                for symbol, spec in entries.items():
                    if service == 'ACCT_ACTIVITY':
                        symbol = self._stream_correl_id
                    groups.setdefault(spec, []).append(symbol)
                for index, ((field_type, fields), symbols) in enumerate(groups.items()):
                    await self._service_chunks_locked(
                        symbols, service, 'SUBS' if index == 0 else 'ADD',
                        field_type, fields=fields, converted=True, record=False)

    @staticmethod
    def _with_required_field(fields, required_field):
        if not fields:
            return fields
        fields = list(fields)
        if required_field not in fields:
            fields.append(required_field)
        return fields

    def _handler_task_done(self, task):
        self._handler_tasks.discard(task)
        self._handler_slots.release()
        if task.cancelled():
            return
        exception = task.exception()
        if exception is not None:
            self._log_handler_exception(
                    'Asynchronous stream handler failed', exception)

    def _schedule_handler(self, service, result):
        if service not in self._sequential_handler_services:
            return asyncio.ensure_future(result)
        lock = self._service_handler_locks.setdefault(service, asyncio.Lock())
        started = False
        disposed = False

        def dispose_unstarted():
            nonlocal disposed
            if started or disposed:
                return
            disposed = True
            close = getattr(result, 'close', None)
            if close is not None:
                close()
            elif isinstance(result, asyncio.Future):
                result.cancel()

        async def run_in_order():
            nonlocal started
            try:
                async with lock:
                    started = True
                    await result
            finally:
                dispose_unstarted()

        task = asyncio.ensure_future(run_in_order())
        # A task cancelled before its first step never enters the finally block.
        def cleanup_unstarted(_):
            dispose_unstarted()
        task.add_done_callback(cleanup_unstarted)
        return task

    def _log_handler_exception(self, message, exception):
        self.logger.error(
                message,
                exc_info=(type(exception), exception,
                          exception.__traceback__))
        if self._on_handler_error is not None:
            try:
                result = self._on_handler_error(exception)
                if inspect.isawaitable(result):
                    close = getattr(result, 'close', None)
                    if close is not None:
                        close()
                    raise TypeError('on_handler_error must be synchronous')
            except Exception:
                self.logger.exception('Stream handler error callback failed')

    async def _dispatch_handlers(self, service, message, *, dispatch_state,
                                 label_message=False):
        generation = self._connection_generation
        if dispatch_state.handlers is None:
            dispatch_state.handlers = tuple(self._handlers[service])
        handlers = dispatch_state.handlers
        owns_message = False
        if label_message and handlers:
            try:
                labeled = handlers[0].label_message(message)
                owns_message = labeled is not message
                message = labeled
            except Exception as exception:
                self._log_handler_exception(
                        'Stream message relabeling failed', exception)
                return

        for index in range(dispatch_state.next_handler, len(handlers)):
            handler = handlers[index]
            # Apply backpressure before invoking a handler or creating a task.
            await self._handler_slots.acquire()
            if generation != self._connection_generation:
                self._handler_slots.release()
                raise StreamClosedError('Stream connection replaced')
            release_slot = True
            try:
                # Relabeling already copied data messages. Give that owned
                # copy to the last handler, after earlier handlers received
                # their independent copies. Keep the wire message untouched
                # so cancellation can safely resume a pending dispatch.
                use_owned_message = owns_message and index == len(handlers) - 1
                handler_message = (
                    message if use_owned_message or not self._copy_messages
                    else _copy_message(message))
                result = handler(handler_message)
                if inspect.isawaitable(result):
                    task = self._schedule_handler(service, result)
                    self._handler_tasks.add(task)
                    task.add_done_callback(self._handler_task_done)
                    release_slot = False
            except Exception as exception:
                self._log_handler_exception(
                        'Synchronous stream handler failed', exception)
            finally:
                dispatch_state.next_handler += 1
                if release_slot:
                    self._handler_slots.release()

    async def handle_message(self):
        # Serialize delivery and dispatch so a cancelled consumer can restore
        # its message before a later consumer starts dispatching newer data.
        generation = self._connection_generation
        async with self._dispatch_lock:
            if generation != self._connection_generation:
                raise StreamClosedError('Stream connection replaced')
            await self._handle_message()

    async def _handle_message(self):
        generation = self._connection_generation
        loop = asyncio.get_running_loop()
        waiter = loop.create_future()
        self._message_waiters.append(waiter)
        if self._overflow_items:
            self._deliver_message(self._overflow_items.popleft())
        self._ensure_reader()

        try:
            msg = await waiter
        except asyncio.CancelledError:
            # Cancellation can race with the reader delivering this waiter.
            # Put an already-delivered message back so the next consumer sees
            # it instead of silently losing it.
            if (generation == self._connection_generation
                    and waiter.done() and not waiter.cancelled()):
                try:
                    delivered_message = waiter.result()
                except BaseException:
                    pass
                else:
                    self._overflow_items.appendleft(delivered_message)
            try:
                self._message_waiters.remove(waiter)
            except ValueError:
                pass
            await self._stop_reader_if_idle()
            raise
        else:
            try:
                self._message_waiters.remove(waiter)
            except ValueError:
                pass

        if isinstance(msg, UnparsableMessage):
            raise msg

        # response
        if 'response' in msg:
            raise UnexpectedResponse(msg,
                                     'unexpected response code during message handling: {}, msg is \'{}\''.format(
                                         msg['response'][0]['content']['code'],
                                         msg['response'][0]['content'].get('msg', '')))

        if not isinstance(msg, _PendingMessage):
            msg = _PendingMessage(msg)
        try:
            while msg.next_event < len(msg.events):
                if generation != self._connection_generation:
                    raise StreamClosedError('Stream connection replaced')
                entry, label_message = msg.events[msg.next_event]
                if not isinstance(entry, dict):
                    self.logger.warning('Ignoring non-object stream entry')
                elif not label_message and 'heartbeat' in entry:
                    pass
                elif isinstance(entry.get('service'), str):
                    await self._dispatch_handlers(
                        entry['service'], entry, dispatch_state=msg,
                        label_message=label_message)
                else:
                    self.logger.warning(
                            'Ignoring stream entry without a valid service: '
                            '%s',
                            LazyLog(lambda entry=entry: redact_json(entry, stream=True)))
                msg.next_event += 1
                msg.next_handler = 0
                msg.handlers = None
        except asyncio.CancelledError:
            # Capacity waits may interrupt a partly dispatched message. Resume
            # at the next uncalled handler instead of dropping it or repeating
            # handlers that already ran.
            if generation == self._connection_generation:
                self._overflow_items.appendleft(msg)
            await self._stop_reader_if_idle()
            raise

    ##########################################################################
    # LOGIN

    def _add_handler(self, service, handler):
        # Equality also recognizes repeated access to the same bound method.
        if not any(existing._func == handler._func
                   for existing in self._handlers[service]):
            self._handlers[service].append(handler)

    def clear_handlers(self, service=None):
        """Remove registered handlers for a service, or all services if omitted.

        Use wire service names such as ``ACCT_ACTIVITY``. Already running
        handlers and dispatch snapshots are unaffected. Registrations otherwise
        survive logout and login.
        """
        if service is None:
            self._handlers.clear()
        else:
            self._handlers.pop(service, None)

    async def login(self, websocket_connect_args=None):
        '''
        Performs initial stream setup:
         * Fetches streaming information from the HTTP client's
           :meth:`~schwab.client.base.BaseClient.get_user_preferences` method
         * Initializes the socket
         * Builds and sends and authentication request
         * Waits for response indicating login success

        All stream operations are available after this method completes.

        :param websocket_connect_args: ``dict`` of additional arguments to pass
                                       to the websocket ``connect`` call. Useful 
                                       for setting timeouts and other connection 
                                       parameters. See `the official 
                                       documentation <https://websockets.readthedocs.io/en/stable/reference/client.html#websockets.client.connect>`__
                                       for details.
        '''

        loop = asyncio.get_running_loop()
        if self._event_loop is not None and self._event_loop is not loop:
            if ((self._reader_task is not None and not self._reader_task.done())
                    or any(not task.done() for task in self._handler_tasks)):
                raise RuntimeError('Close the stream on its original event loop '
                                   'before using it on another event loop')
            self._handler_slots = asyncio.Semaphore(self._max_pending_handler_tasks)
            self._send_lock = asyncio.Lock()
            self._dispatch_lock = asyncio.Lock()
            self._reader_wakeup = asyncio.Event()
            self._service_handler_locks.clear()
            self._subscription_locks.clear()
        self._event_loop = loop

        # Fetch required data and initialize the client
        if isinstance(self._client, Client) and not self._offload_sync_preferences:
            warnings.warn(
                'Synchronous preference lookup blocks the event loop; use an '
                'async client or offload_sync_preferences=True when safe.',
                RuntimeWarning, stacklevel=2)
        if self._offload_sync_preferences:
            r = await asyncio.to_thread(self._client.get_user_preferences)
        else:
            r = self._client.get_user_preferences()

        # We don't actually know whether the client is synchronous or
        # asynchronous, so work around by awaiting the response if necessary
        if inspect.isawaitable(r):
            r = await r
        if r.status_code != httpx.codes.OK:
            r.raise_for_status()
            # Other 2xx codes (such as 204) carry no preferences to parse.
            raise httpx.HTTPStatusError(
                'Unexpected status code {} fetching user preferences'.format(
                    r.status_code),
                request=getattr(r, '_request', None), response=r)
        r = r.json()

        await self._init_from_preferences(
                r, websocket_connect_args if websocket_connect_args else {})

        try:
            # Build and send the request object
            request_parameters = {
                'Authorization': self._client.token_metadata.token['access_token'],
                'SchwabClientChannel': self._stream_channel,
                'SchwabClientFunctionId': self._stream_function_id,
            }
            request, request_id = self._make_request(
                service='ADMIN', command='LOGIN',
                parameters=request_parameters)
            async with self._send_lock:
                await self._send_request_and_await_response(
                        request, request_id, 'ADMIN', 'LOGIN',
                        acquire_send_lock=False)
        except BaseException:
            try:
                await self._close_connection()
            except Exception:
                self.logger.exception('Failed to close stream after login failure')
            raise

    ##########################################################################
    # LOGOUT

    async def logout(self):
        '''
        Performs a logout operation on the stream. After this method is called,
        no further stream operations are possible. The client must be
        re-initialized with :meth:`login` to perform further operations.
        '''
        request, request_id = self._make_request(
            service='ADMIN', command='LOGOUT',
            parameters={})
        try:
            await self._send_request_and_await_response(
                    request, request_id, 'ADMIN', 'LOGOUT')
        finally:
            await self._close_connection()

    ##########################################################################
    # ACCT_ACTIVITY

    class AccountActivityFields(_BaseFieldEnum):
        '''
        Data fields for equity account activity. Primarily an implementation detail
        and not used in client code. Provided here as documentation for key
        values stored returned in the stream messages.
        '''

        #: Passed back to the client from the request to identify a subscription this response belongs to.
        SUBSCRIPTION_KEY = 0

        #: Account Number that the activity occurred on.
        ACCOUNT = 1

        #: Message Type that dictates the format of the Message Data field.
        MESSAGE_TYPE = 2

        #: The core data for the message. Either JSON-formatted data describing the update, NULL in some cases,
        #: or plain text in case of ERROR.
        MESSAGE_DATA = 3

    async def account_activity_sub(self):
        '''
        Subscribe to account activity associated with this streaming
        connection. See :class:`AccountActivityFields` for more info.
        '''
        await self._service_op(
            [self._stream_correl_id], 'ACCT_ACTIVITY', 'SUBS',
            self.AccountActivityFields)

    async def account_activity_unsubs(self):
        '''
        Un-Subscribe from account activity associated with this streaming
        connection. See :class:`AccountActivityFields` for more info.
        '''
        await self._service_op([self._stream_correl_id], 'ACCT_ACTIVITY', 'UNSUBS')

    def add_account_activity_handler(self, handler):
        '''
        Adds a handler to the account activity subscription. See
        :ref:`registering_handlers` for details.
        '''
        self._add_handler('ACCT_ACTIVITY', _Handler(handler,
                                                        self.AccountActivityFields))

    ##########################################################################
    # CHART_EQUITY

    class ChartEquityFields(_BaseFieldEnum):
        '''
        Data fields for equity OHLCV data. Primarily an implementation detail
        and not used in client code. Provided here as documentation for key
        values stored returned in the stream messages.
        '''

        #: Ticker symbol
        SYMBOL = 0

        #: Sequence number
        SEQUENCE = 1

        #: Today's open price
        OPEN_PRICE = 2

        #: Today's high price
        HIGH_PRICE = 3

        #: Today's low price
        LOW_PRICE = 4

        #: Previous day's close price
        CLOSE_PRICE = 5

        #: Today's trading volume
        VOLUME = 6

        #: Chart timestamp
        CHART_TIME_MILLIS = 7

        #: Chart day
        CHART_DAY = 8

    async def chart_equity_subs(self, symbols):
        '''
        Subscribe to equity charts. Behavior is undefined if called multiple
        times.

        :param symbols: Equity symbols to subscribe to.'''
        await self._service_op(
            symbols, 'CHART_EQUITY', 'SUBS', self.ChartEquityFields,
            fields=self.ChartEquityFields.all_fields())

    async def chart_equity_unsubs(self, symbols):
        '''
        Un-Subscribe to equity charts. Behavior is undefined if called multiple
        times.

        :param symbols: Equity symbols to subscribe to.'''
        await self._service_op(symbols, 'CHART_EQUITY', 'UNSUBS')

    async def chart_equity_add(self, symbols):
        '''
        Add a symbol to the equity charts subscription. Behavior is undefined
        if called before :meth:`chart_equity_subs`.

        :param symbols: Equity symbols to add to the subscription.
        '''
        await self._service_op(
            symbols, 'CHART_EQUITY', 'ADD', self.ChartEquityFields,
            fields=self.ChartEquityFields.all_fields())

    def add_chart_equity_handler(self, handler):
        '''
        Adds a handler to the equity chart subscription. See
        :ref:`registering_handlers` for details.
        '''
        self._add_handler('CHART_EQUITY', _Handler(handler,
                                                       self.ChartEquityFields))

    ##########################################################################
    # CHART_FUTURES

    class ChartFuturesFields(_BaseFieldEnum):
        '''
        Data fields for equity OHLCV data. Primarily an implementation detail
        and not used in client code. Provided here as documentation for key
        values stored returned in the stream messages.
        '''

        #: Ticker symbol in upper case.
        SYMBOL = 0

        #: Milliseconds since Epoch
        CHART_TIME_MILLIS = 1

        #: Opening price for the minute
        OPEN_PRICE = 2

        #: Highest price for the minute
        HIGH_PRICE = 3

        #: Chart's lowest price for the minute
        LOW_PRICE = 4

        #: Closing price for the minute
        CLOSE_PRICE = 5

        #: Total volume for the minute
        VOLUME = 6

    async def chart_futures_subs(self, symbols):
        '''
        Subscribe to futures charts. Behavior is undefined if called multiple
        times.

        :param symbols: Futures symbols to subscribe to.
        '''
        await self._service_op(
            symbols, 'CHART_FUTURES', 'SUBS', self.ChartFuturesFields,
            fields=self.ChartFuturesFields.all_fields())

    async def chart_futures_unsubs(self, symbols):
        '''
        Un-Subscribe to futures charts. Behavior is undefined if called multiple
        times.

        :param symbols: Futures symbols to subscribe to.
        '''
        await self._service_op(symbols, 'CHART_FUTURES', 'UNSUBS')

    async def chart_futures_add(self, symbols):
        '''
        Add a symbol to the futures chart subscription. Behavior is undefined
        if called before :meth:`chart_futures_subs`.

        :param symbols: Futures symbols to add to the subscription.
        '''
        await self._service_op(
            symbols, 'CHART_FUTURES', 'ADD', self.ChartFuturesFields,
            fields=self.ChartFuturesFields.all_fields())

    def add_chart_futures_handler(self, handler):
        '''
        Adds a handler to the futures chart subscription. See
        :ref:`registering_handlers` for details.
        '''
        self._add_handler('CHART_FUTURES', _Handler(handler,
                                                        self.ChartFuturesFields))

    ##########################################################################
    # LEVELONE_EQUITIES

    class LevelOneEquityFields(_BaseFieldEnum):
        '''
        Fields for equity quotes.
        '''

        #: Ticker symbol
        SYMBOL = 0

        #: Bid price
        BID_PRICE = 1

        #: Ask price
        ASK_PRICE = 2

        #: Last trade price
        LAST_PRICE = 3

        #: Size of the highest bid
        BID_SIZE = 4

        #: Size of the lowest ask
        ASK_SIZE = 5

        #: Exchange ID of the lowest ask
        ASK_ID = 6

        #: Exchange ID of the highest bid
        BID_ID = 7

        #: Total volume trade to date
        TOTAL_VOLUME = 8

        #: Size of the last trade
        LAST_SIZE = 9

        #: Daily high price
        HIGH_PRICE = 10

        #: Daily low price
        LOW_PRICE = 11

        #: Previous close price
        CLOSE_PRICE = 12

        #: Exchange ID
        EXCHANGE_ID = 13

        #: Is this equity marginable?
        MARGINABLE = 14

        #: Description
        DESCRIPTION = 15

        #: Exchange ID of the last trade
        LAST_ID = 16

        #: Today's open price
        OPEN_PRICE = 17

        #: Net change
        NET_CHANGE = 18

        #: 52 week high price
        HIGH_PRICE_52_WEEK = 19

        #: 52 week low price
        LOW_PRICE_52_WEEK = 20

        #: P/E ratio
        PE_RATIO = 21

        #: Dividend amount
        DIVIDEND_AMOUNT = 22

        #: Dividend yield
        DIVIDEND_YIELD = 23

        #: ETF net asset value
        NAV = 24

        #: Exchange name
        EXCHANGE_NAME = 25

        #: Dividend date
        DIVIDEND_DATE = 26

        #: Is this a regular market quote?
        REGULAR_MARKET_QUOTE = 27

        #: Is this a regular market trade?
        REGULAR_MARKET_TRADE = 28

        #: Regular market last price
        REGULAR_MARKET_LAST_PRICE = 29

        #: Regular market last size
        REGULAR_MARKET_LAST_SIZE = 30

        #: Regular market net change
        REGULAR_MARKET_NET_CHANGE = 31

        #: Security status
        SECURITY_STATUS = 32

        #: Mark
        MARK = 33

        #: Quote time in milliseconds
        QUOTE_TIME_MILLIS = 34

        #: Last trade time in milliseconds
        TRADE_TIME_MILLIS = 35

        #: Regular market trade time in milliseconds
        REGULAR_MARKET_TRADE_MILLIS = 36

        #: Bid time in millis
        BID_TIME_MILLIS = 37

        #: Ask time in millis
        ASK_TIME_MILLIS = 38

        #: Ask MIC ID
        ASK_MIC_ID = 39

        #: Bid MIC ID
        BID_MIC_ID = 40

        #: Last trade MIC ID
        LAST_MIC_ID = 41

        #: Net change in percent
        NET_CHANGE_PERCENT = 42

        #: Regular market change in percent
        REGULAR_MARKET_CHANGE_PERCENT = 43

        #: Mark change
        MARK_CHANGE = 44

        #: Mark change in percent
        MARK_CHANGE_PERCENT = 45

        #: HTB quantity
        HTB_QUANTITY = 46

        #: HTB rate
        HTB_RATE = 47

        #: Is this equity hard to borrow?
        HARD_TO_BORROW = 48

        #: Is this equity shortable
        IS_SHORTABLE = 49

        #: Post market net change
        POST_MARKET_NET_CHANGE = 50

        #: Post market net change percent
        POST_MARKET_NET_CHANGE_PERCENT = 51

    async def level_one_equity_subs(self, symbols, *, fields=None):
        '''
        Subscribe to level one equity quote data.

        :param symbols: Equity symbols to receive quotes for
        :param fields: Iterable of :class:`LevelOneEquityFields` representing
                       the fields to return in streaming entries. If unset, all
                       fields will be requested.
        '''
        fields = self._with_required_field(
                fields, self.LevelOneEquityFields.SYMBOL)
        await self._service_op(
            symbols, 'LEVELONE_EQUITIES', 'SUBS', self.LevelOneEquityFields,
            fields=fields)

    async def level_one_equity_unsubs(self, symbols):
        '''
        Un-Subscribe to level one equity quote data.

        :param symbols: Equity symbols to receive quotes for
        '''

        await self._service_op(symbols, 'LEVELONE_EQUITIES', 'UNSUBS')

    async def level_one_equity_add(self, symbols, *, fields=None):
        '''
        Add symbols to the list to receive quotes for.

        :param symbols: Equity symbols to receive quotes for
        :param fields: Iterable of :class:`LevelOneEquityFields` representing
                       the fields to return in streaming entries. If unset, all
                       fields will be requested.
        '''
        fields = self._with_required_field(
                fields, self.LevelOneEquityFields.SYMBOL)
        await self._service_op(
            symbols, 'LEVELONE_EQUITIES', 'ADD',
            self.LevelOneEquityFields, fields=fields)

    def add_level_one_equity_handler(self, handler):
        '''
        Register a function to handle level one equity quotes as they are sent.
        See :ref:`registering_handlers` for details.
        '''
        self._add_handler('LEVELONE_EQUITIES',
                _Handler(handler, self.LevelOneEquityFields))

    ##########################################################################
    # LEVELONE_OPTIONS

    class LevelOneOptionFields(_BaseFieldEnum):
        '''
        '''

        #: Option symbol
        SYMBOL = 0

        #: Description
        DESCRIPTION = 1

        #: Highest bid price
        BID_PRICE = 2

        #: Lowest ask price
        ASK_PRICE = 3

        #: Last trade price
        LAST_PRICE = 4

        #: Today's high price
        HIGH_PRICE = 5

        #: Today's low price
        LOW_PRICE = 6

        #: Last close price
        CLOSE_PRICE = 7

        #: Today's total volume
        TOTAL_VOLUME = 8

        #: Open interest
        OPEN_INTEREST = 9

        #: Volatility
        VOLATILITY = 10

        #: Money intrinsic value
        MONEY_INTRINSIC_VALUE = 11

        #: Expiration year
        EXPIRATION_YEAR = 12

        #: Multiplier
        MULTIPLIER = 13

        #: Digits
        DIGITS = 14

        #: Open price
        OPEN_PRICE = 15

        #: Highest bid size
        BID_SIZE = 16

        #: Lowest ask size
        ASK_SIZE = 17

        #: Last trade size
        LAST_SIZE = 18

        #: Net change
        NET_CHANGE = 19

        #: Contract strike price
        STRIKE_PRICE = 20

        #: Deprecated alias for :attr:`STRIKE_PRICE`
        STRIKE_TYPE = 20

        #: Contract type
        CONTRACT_TYPE = 21

        #: Underlying symbol
        UNDERLYING = 22

        #: Expiration month
        EXPIRATION_MONTH = 23

        #: Deliverables
        DELIVERABLES = 24

        #: Time value
        TIME_VALUE = 25

        #: Expiration day
        EXPIRATION_DAY = 26

        #: Days to expiration
        DAYS_TO_EXPIRATION = 27

        #: Delta
        DELTA = 28

        #: Gamma
        GAMMA = 29

        #: Theta
        THETA = 30

        #: Vega
        VEGA = 31

        #: Rho
        RHO = 32

        #: Security status
        SECURITY_STATUS = 33

        #: Theoretical option value
        THEORETICAL_OPTION_VALUE = 34

        #: Underlying price
        UNDERLYING_PRICE = 35

        #: UV expiration type
        UV_EXPIRATION_TYPE = 36

        #: Mark
        MARK = 37

        #: Quote time in millis
        QUOTE_TIME_MILLIS = 38

        #: Last trade time in millis
        TRADE_TIME_MILLIS = 39

        #: Exchange ID
        EXCHANGE_ID = 40

        #: Exchange name
        EXCHANGE_NAME = 41

        #: Last trading day
        LAST_TRADING_DAY = 42

        #: Settlement type
        SETTLEMENT_TYPE = 43

        #: Net percent change
        NET_PERCENT_CHANGE = 44

        #: Mark change
        MARK_CHANGE = 45

        #: Mark change in percent
        MARK_CHANGE_PERCENT = 46

        #: Implied yield
        IMPLIED_YIELD = 47

        #: Is penny stock?
        IS_PENNY = 48

        #: Option root
        OPTION_ROOT = 49

        #: 52 week high price
        HIGH_PRICE_52_WEEK = 50

        #: 52 week low price
        LOW_PRICE_52_WEEK = 51

        #: Indicative asking price
        INDICATIVE_ASKING_PRICE = 52

        #: Indicative bid price
        INDICATIVE_BID_PRICE = 53

        #: Indicative quote time
        INDICATIVE_QUOTE_TIME = 54

        #: Exercise type
        EXERCISE_TYPE = 55

    async def level_one_option_subs(self, symbols, *, fields=None):
        '''
        Subscribe to level one option quote data.

        :param symbols: Option symbols to receive quotes for
        :param fields: Iterable of :class:`LevelOneOptionFields` representing
                       the fields to return in streaming entries. If unset, all
                       fields will be requested.
        '''
        fields = self._with_required_field(
                fields, self.LevelOneOptionFields.SYMBOL)
        await self._service_op(
            symbols, 'LEVELONE_OPTIONS', 'SUBS', self.LevelOneOptionFields,
            fields=fields)

    async def level_one_option_unsubs(self, symbols):
        '''
        Un-Subscribe to level one option quote data.

        :param symbols: Option symbols to receive quotes for
        '''
        await self._service_op(symbols, 'LEVELONE_OPTIONS', 'UNSUBS')

    async def level_one_option_add(self, symbols, *, fields=None):
        '''
        Add symbols to the list to receive quotes for.

        :param symbols: Option symbols to add to list to receive quotes for
        :param fields: Iterable of :class:`LevelOneOptionFields` representing
                       the fields to return in streaming entries. If unset, all
                       fields will be requested.
        '''
        fields = self._with_required_field(
                fields, self.LevelOneOptionFields.SYMBOL)
        await self._service_op(
            symbols, 'LEVELONE_OPTIONS', 'ADD',
            self.LevelOneOptionFields, fields=fields)

    def add_level_one_option_handler(self, handler):
        '''
        Register a function to handle level one options quotes as they are sent.
        See :ref:`registering_handlers` for details.
        '''
        self._add_handler('LEVELONE_OPTIONS',
                _Handler(handler, self.LevelOneOptionFields))

    ##########################################################################
    # LEVELONE_FUTURES

    class LevelOneFuturesFields(_BaseFieldEnum):
        '''
        '''

        #: Ticker symbol in upper case.
        SYMBOL = 0

        #: Current Best Bid Price
        BID_PRICE = 1

        #: Current Best Ask Price
        ASK_PRICE = 2

        #: Price at which the last trade was matched
        LAST_PRICE = 3

        #: Number of contracts for bid
        BID_SIZE = 4

        #: Number of contracts for ask
        ASK_SIZE = 5

        #: Exchange with the best bid
        BID_ID = 6

        #: Exchange with the best ask
        ASK_ID = 7

        #: Aggregated contracts traded throughout the day, including pre/post market hours.
        TOTAL_VOLUME = 8

        #: Number of contracts traded with last trade
        LAST_SIZE = 9

        #: Time of the last quote in milliseconds since epoch
        QUOTE_TIME_MILLIS = 10

        #: Time of the last trade in milliseconds since epoch
        TRADE_TIME_MILLIS = 11

        #: Day's high trade price
        HIGH_PRICE = 12

        #: Day's low trade price
        LOW_PRICE = 13

        #: Previous day's closing price
        CLOSE_PRICE = 14

        #: Primary "listing" Exchange
        EXCHANGE_ID = 15

        #: Description of the product
        DESCRIPTION = 16

        #: Exchange where last trade was executed
        LAST_ID = 17

        #: Day's Open Price
        OPEN_PRICE = 18

        #: Current Last-Prev Close
        NET_CHANGE = 19

        #: Current percent change
        FUTURE_CHANGE_PERCENT = 20

        #: Name of exchange
        EXCHANGE_NAME = 21

        #: Trading status of the symbol
        SECURITY_STATUS = 22

        #: The total number of futures contracts that are not closed or delivered on a particular day
        OPEN_INTEREST = 23

        #: Mark-to-Market value is calculated daily using current prices to determine profit/loss
        MARK = 24

        #: Minimum price movement
        TICK = 25

        #: Minimum amount that the price of the market can change
        TICK_AMOUNT = 26

        #: Futures product
        PRODUCT = 27

        #: Display in fraction or decimal format.
        FUTURE_PRICE_FORMAT = 28

        #: Trading hours
        FUTURE_TRADING_HOURS = 29

        #: Flag to indicate if this future contract is tradable
        FUTURE_IS_TRADABLE = 30

        #: Point value
        FUTURE_MULTIPLIER = 31

        #: Indicates if this contract is active
        FUTURE_IS_ACTIVE = 32

        #: Closing price
        FUTURE_SETTLEMENT_PRICE = 33

        #: Symbol of the active contract
        FUTURE_ACTIVE_SYMBOL = 34

        #: Expiration date of this contract
        FUTURE_EXPIRATION_DATE = 35

        #: Expiration Style
        EXPIRATION_STYLE = 36

        #: Time of the last ask-side quote in milliseconds since epoch
        ASK_TIME_MILLIS = 37

        #: Time of the last bid-side quote in milliseconds since epoch
        BID_TIME_MILLIS = 38

        #: Indicates if this contract has quoted during the active session
        QUOTED_IN_SESSION = 39

        #: Expiration date of this contract
        SETTLEMENT_DATE = 40

    async def level_one_futures_subs(self, symbols, *, fields=None):
        '''
        Subscribe to level one futures quote data.

        :param symbols: Futures symbols to receive quotes for
        :param fields: Iterable of :class:`LevelOneFuturesFields` representing
                       the fields to return in streaming entries. If unset, all
                       fields will be requested.
        '''
        fields = self._with_required_field(
                fields, self.LevelOneFuturesFields.SYMBOL)
        await self._service_op(
            symbols, 'LEVELONE_FUTURES', 'SUBS', self.LevelOneFuturesFields,
            fields=fields)

    async def level_one_futures_unsubs(self, symbols):
        '''
        Un-Subscribe to level one futures quote data.

        :param symbols: Futures symbols to receive quotes for
        '''

        await self._service_op(symbols, 'LEVELONE_FUTURES', 'UNSUBS')

    async def level_one_futures_add(self, symbols, *, fields=None):
        '''
        Add symbols to the list to receive quotes for.

        :param symbols: Futures symbols to add to the list to receive quotes for
        :param fields: Iterable of :class:`LevelOneFuturesFields` representing
                       the fields to return in streaming entries. If unset, all
                       fields will be requested.
        '''
        fields = self._with_required_field(
                fields, self.LevelOneFuturesFields.SYMBOL)
        await self._service_op(
            symbols, 'LEVELONE_FUTURES', 'ADD',
            self.LevelOneFuturesFields, fields=fields)

    def add_level_one_futures_handler(self, handler):
        '''
        Register a function to handle level one futures quotes as they are sent.
        See :ref:`registering_handlers` for details.
        '''
        self._add_handler('LEVELONE_FUTURES',
            _Handler(handler, self.LevelOneFuturesFields))

    ##########################################################################
    # LEVELONE_FOREX

    class LevelOneForexFields(_BaseFieldEnum):
        '''
        '''

        #: Ticker symbol in upper case.
        SYMBOL = 0

        #: Current Bid Price
        BID_PRICE = 1

        #: Current Ask Price
        ASK_PRICE = 2

        #: Price at which the last trade was matched
        LAST_PRICE = 3

        #: Number of currency pairs for bid
        BID_SIZE = 4

        #: Number of currency pairs for ask
        ASK_SIZE = 5

        #: Aggregated currency pairs traded throughout the day, including pre/post market hours.
        TOTAL_VOLUME = 6

        #: Number of currency pairs traded with last trade
        LAST_SIZE = 7

        #: Trade time of the last quote in milliseconds since epoch
        QUOTE_TIME_MILLIS = 8

        #: Trade time of the last trade in milliseconds since epoch
        TRADE_TIME_MILLIS = 9

        #: Day's high trade price
        HIGH_PRICE = 10

        #: Day's low trade price
        LOW_PRICE = 11

        #: Previous day's closing price
        CLOSE_PRICE = 12

        #: Exchange Id
        EXCHANGE_ID = 13

        #: Description of the product
        DESCRIPTION = 14

        #: Day's Open Price
        OPEN_PRICE = 15

        #: Current Last-Prev Close
        NET_CHANGE = 16

        #: Current percent change
        CHANGE_PERCENT = 17

        #: Name of exchange
        EXCHANGE_NAME = 18

        #: Valid decimal points
        DIGITS = 19

        #: Trading status of the symbol
        SECURITY_STATUS = 20

        #: Minimum price movement
        TICK = 21

        #: Minimum amount that the price of the market can change
        TICK_AMOUNT = 22

        #: Product name
        PRODUCT = 23

        #: Trading hours
        TRADING_HOURS = 24

        #: Flag to indicate if this forex is tradable
        IS_TRADABLE = 25

        #: Market Maker
        MARKET_MAKER = 26

        #: Highest price traded in the past 12 months, or 52 weeks
        HIGH_PRICE_52_WEEK = 27

        #: Lowest price traded in the past 12 months, or 52 weeks
        LOW_PRICE_52_WEEK = 28

        #: Mark-to-Market value is calculated daily using current prices to determine profit/loss
        MARK = 29

    async def level_one_forex_subs(self, symbols, *, fields=None):
        '''
        Subscribe to level one forex quote data.

        :param symbols: Forex symbols to receive quotes for
        :param fields: Iterable of :class:`LevelOneForexFields` representing
                       the fields to return in streaming entries. If unset, all
                       fields will be requested.
        '''
        fields = self._with_required_field(
                fields, self.LevelOneForexFields.SYMBOL)
        await self._service_op(
            symbols, 'LEVELONE_FOREX', 'SUBS', self.LevelOneForexFields,
            fields=fields)

    async def level_one_forex_unsubs(self, symbols):
        '''
        Un-Subscribe to level one forex quote data.

        :param symbols: Forex symbols to receive quotes for
        '''

        await self._service_op(symbols, 'LEVELONE_FOREX', 'UNSUBS')

    async def level_one_forex_add(self, symbols, *, fields=None):
        '''
        Add symbols to the list to receive quotes for.

        :param symbols: Forex symbols to add to list to receive quotes for
        :param fields: Iterable of :class:`LevelOneForexFields` representing
                       the fields to return in streaming entries. If unset, all
                       fields will be requested.

        '''
        fields = self._with_required_field(
                fields, self.LevelOneForexFields.SYMBOL)
        await self._service_op(
            symbols, 'LEVELONE_FOREX', 'ADD',
            self.LevelOneForexFields, fields=fields)

    def add_level_one_forex_handler(self, handler):
        '''
        Register a function to handle level one forex quotes as they are sent.
        See :ref:`registering_handlers` for details.
        '''
        self._add_handler('LEVELONE_FOREX', _Handler(handler,
                                                         self.LevelOneForexFields))

    ##########################################################################
    # LEVELONE_FUTURES_OPTIONS

    class LevelOneFuturesOptionsFields(_BaseFieldEnum):
        '''
        '''

        #: Ticker symbol in upper case.
        SYMBOL = 0

        #: Current Bid Price
        BID_PRICE = 1

        #: Current Ask Price
        ASK_PRICE = 2

        #: Price at which the last trade was matched
        LAST_PRICE = 3

        #: Number of contracts for bid
        BID_SIZE = 4

        #: Number of contracts for ask
        ASK_SIZE = 5

        #: Exchange with the bid
        BID_ID = 6

        #: Exchange with the ask
        ASK_ID = 7

        #: Aggregated contracts traded throughout the day, including pre/post market hours.
        TOTAL_VOLUME = 8

        #: Number of contracts traded with last trade
        LAST_SIZE = 9

        #: Trade time of the last quote in milliseconds since epoch
        QUOTE_TIME_MILLIS = 10

        #: Trade time of the last trade in milliseconds since epoch
        TRADE_TIME_MILLIS = 11

        #: Day's high trade price
        HIGH_PRICE = 12

        #: Day's low trade price
        LOW_PRICE = 13

        #: Previous day's closing price
        CLOSE_PRICE = 14

        #: Exchange where last trade was executed
        LAST_ID = 15

        #: Description of the product
        DESCRIPTION = 16

        #: Day's Open Price
        OPEN_PRICE = 17

        #: Open Interest
        OPEN_INTEREST = 18

        #: Mark-to-Market value is calculated daily using current prices to determine profit/loss
        MARK = 19

        #: Minimum price movement
        TICK = 20

        #: Minimum amount that the price of the market can change
        TICK_AMOUNT = 21

        #: Point value
        FUTURE_MULTIPLIER = 22

        #: Closing price
        FUTURE_SETTLEMENT_PRICE = 23

        #: Underlying symbol
        UNDERLYING_SYMBOL = 24

        #: Strike Price
        STRIKE_PRICE = 25

        #: Expiration date of this contract
        FUTURE_EXPIRATION_DATE = 26

        #: Expiration Style
        EXPIRATION_STYLE = 27

        #: Contract Type
        CONTRACT_TYPE = 28

        #: Security Status
        SECURITY_STATUS = 29

        #: Exchange character
        EXCHANGE_ID = 30

        #: Display name of exchange
        EXCHANGE_NAME = 31

    async def level_one_futures_options_subs(self, symbols, *, fields=None):
        '''
        Subscribe to level one futures options quote data.

        :param symbols: Futures options symbols to receive quotes for
        :param fields: Iterable of :class:`LevelOneFuturesOptionsFields`
                       representing the fields to return in streaming entries.
                       If unset, all fields will be requested.
        '''
        fields = self._with_required_field(
                fields, self.LevelOneFuturesOptionsFields.SYMBOL)
        await self._service_op(
            symbols, 'LEVELONE_FUTURES_OPTIONS', 'SUBS',
            self.LevelOneFuturesOptionsFields, fields=fields)

    async def level_one_futures_options_unsubs(self, symbols):
        '''
        Un-Subscribe to level one futures options quote data.

        :param symbols: Futures options symbols to receive quotes for
        '''

        await self._service_op(symbols, 'LEVELONE_FUTURES_OPTIONS', 'UNSUBS')

    async def level_one_futures_options_add(self, symbols, *, fields=None):
        '''
        Add symbols to the list to receive quotes for.

        :param symbols: Futures options symbols add to list to receive quotes for
        :param fields: Iterable of :class:`LevelOneFuturesOptionsFields`
                       representing the fields to return in streaming entries.
                       If unset, all fields will be requested.
        '''
        fields = self._with_required_field(
                fields, self.LevelOneFuturesOptionsFields.SYMBOL)
        await self._service_op(
            symbols, 'LEVELONE_FUTURES_OPTIONS', 'ADD',
            self.LevelOneFuturesOptionsFields, fields=fields)

    def add_level_one_futures_options_handler(self, handler):
        '''
        Register a function to handle level one futures options quotes as they
        are sent. See :ref:`registering_handlers` for details.
        '''
        self._add_handler('LEVELONE_FUTURES_OPTIONS',
            _Handler(handler, self.LevelOneFuturesOptionsFields))

    ##########################################################################
    # Common book utilities

    class BookFields(_BaseFieldEnum):
        SYMBOL = 0
        BOOK_TIME = 1
        BIDS = 2
        ASKS = 3

    class BidFields(_BaseFieldEnum):
        BID_PRICE = 0
        TOTAL_VOLUME = 1
        NUM_BIDS = 2
        BIDS = 3

    class PerExchangeBidFields(_BaseFieldEnum):
        EXCHANGE = 0
        BID_VOLUME = 1
        SEQUENCE = 2

    class AskFields(_BaseFieldEnum):
        ASK_PRICE = 0
        TOTAL_VOLUME = 1
        NUM_ASKS = 2
        ASKS = 3

    class PerExchangeAskFields(_BaseFieldEnum):
        EXCHANGE = 0
        ASK_VOLUME = 1
        SEQUENCE = 2

    class _BookHandler(_Handler):
        def label_message(self, msg):
            # Relabel top-level fields. The result is a fresh copy whenever
            # there is content to relabel, so nested levels can be relabeled
            # in place below.
            new_msg = super().label_message(msg)
            content = new_msg.get('content') if isinstance(new_msg, dict) else None
            if not isinstance(content, list):
                return new_msg

            # Relabel the price levels and their per-exchange entries that
            # are present. Partial levels are kept rather than dropping the
            # whole message.
            sides = (
                ('BIDS', StreamClient.BidFields,
                 StreamClient.PerExchangeBidFields),
                ('ASKS', StreamClient.AskFields,
                 StreamClient.PerExchangeAskFields),
            )
            for entry in content:
                if not isinstance(entry, dict):
                    continue
                for side, level_fields, exchange_fields in sides:
                    levels = entry.get(side)
                    if not isinstance(levels, list):
                        continue
                    for level in levels:
                        if not isinstance(level, dict):
                            continue
                        level_fields.relabel_message(level, level)
                        exchanges = level.get(side)
                        if not isinstance(exchanges, list):
                            continue
                        for exchange in exchanges:
                            if isinstance(exchange, dict):
                                exchange_fields.relabel_message(
                                    exchange, exchange)

            return new_msg

    ##########################################################################
    # NYSE_BOOK

    async def nyse_book_subs(self, symbols):
        '''
        Subscribe to the NYSE level two order book.

        :param symbols: NYSE symbols to subscribe to.
        '''
        await self._service_op(
            symbols, 'NYSE_BOOK', 'SUBS',
            self.BookFields, fields=self.BookFields.all_fields())

    async def nyse_book_unsubs(self, symbols):
        '''
        Un-Subscribe to the NYSE level two order book.

        :param symbols: NYSE symbols to unsubscribe from.
        '''
        await self._service_op(symbols, 'NYSE_BOOK', 'UNSUBS')

    async def nyse_book_add(self, symbols):
        '''
        Add to the NYSE level two order book.

        :param symbols: NYSE symbols to add to the subscription.
        '''
        await self._service_op(symbols, 'NYSE_BOOK', 'ADD', self.BookFields)

    def add_nyse_book_handler(self, handler):
        '''
        Register a function to handle level two NYSE book data as it is updated
        See :ref:`registering_handlers` for details.
        '''
        self._add_handler('NYSE_BOOK',
            self._BookHandler(handler, self.BookFields))

    ##########################################################################
    # NASDAQ_BOOK

    async def nasdaq_book_subs(self, symbols):
        '''
        Subscribe to the NASDAQ level two order book.

        :param symbols: NASDAQ symbols to subscribe to.
        '''
        await self._service_op(symbols, 'NASDAQ_BOOK', 'SUBS',
                               self.BookFields,
                               fields=self.BookFields.all_fields())

    async def nasdaq_book_unsubs(self, symbols):
        '''
        Un-Subscribe to the NASDAQ level two order book.

        :param symbols: NASDAQ symbols to unsubscribe from.
        '''
        await self._service_op(symbols, 'NASDAQ_BOOK', 'UNSUBS')

    async def nasdaq_book_add(self, symbols):
        '''
        Add to the NASDAQ level two order book.

        :param symbols: NASDAQ symbols to add to the subscription.
        '''
        await self._service_op(symbols, 'NASDAQ_BOOK', 'ADD', self.BookFields)

    def add_nasdaq_book_handler(self, handler):
        '''
        Register a function to handle level two NASDAQ book data as it is
        updated See :ref:`registering_handlers` for details.
        '''
        self._add_handler('NASDAQ_BOOK',
            self._BookHandler(handler, self.BookFields))

    ##########################################################################
    # OPTIONS_BOOK

    async def options_book_subs(self, symbols):
        '''
        Subscribe to the level two order book for options.

        :param symbols: Option symbols to subscribe to.
        '''
        await self._service_op(symbols, 'OPTIONS_BOOK', 'SUBS',
                               self.BookFields,
                               fields=self.BookFields.all_fields())

    async def options_book_unsubs(self, symbols):
        '''
        Un-Subscribe to the level two order book for options.

        :param symbols: Option symbols to unsubscribe from.
        '''
        await self._service_op(symbols, 'OPTIONS_BOOK', 'UNSUBS')

    async def options_book_add(self, symbols):
        '''
        Add to the level two order book for options.

        :param symbols: Option symbols to add to the subscription.
        '''
        await self._service_op(symbols, 'OPTIONS_BOOK', 'ADD', self.BookFields)

    def add_options_book_handler(self, handler):
        '''
        Register a function to handle level two options book data as it is
        updated See :ref:`registering_handlers` for details.
        '''
        self._add_handler('OPTIONS_BOOK',
            self._BookHandler(handler, self.BookFields))

    ##########################################################################
    # SCREENER_EQUITY/SCREENER_OPTION

    class ScreenerFields(_BaseFieldEnum):
        #: The symbol used to look up either actives, gainers or losers
        SYMBOL = 0

        #: Market snapshot timestamp in milliseconds since Epoch
        TIMESTAMP = 1

        #: Field to sort on
        SORT_FIELD = 2

        #: Frequency of data to sort
        FREQUENCY = 3

        #: Array of fields
        ITEMS = 4

    async def screener_equity_subs(self, symbols):
        '''
        Subscribe to Screener Equity.

        :param symbols: Equity symbols to subscribe to.
        '''
        await self._service_op(symbols, 'SCREENER_EQUITY', 'SUBS', self.ScreenerFields)

    async def screener_equity_unsubs(self, symbols):
        '''
        Un-Subscribe to Screener Equity.

        :param symbols: Equity symbols to unsubscribe from.
        '''
        await self._service_op(symbols, 'SCREENER_EQUITY', 'UNSUBS')

    async def screener_equity_add(self, symbols):
        '''
        Add symbols to the Screener Equity list.

        :param symbols: Equity symbols to add to the subscription.
        '''
        await self._service_op(symbols, 'SCREENER_EQUITY', 'ADD', self.ScreenerFields)

    def add_screener_equity_handler(self, handler):
        '''
        Register a function to handle Screener Equity data as it is
        updated See :ref:`registering_handlers` for details.
        '''
        self._add_handler('SCREENER_EQUITY',
            _Handler(handler, self.ScreenerFields))

    async def screener_option_subs(self, symbols):
        '''
        Subscribe to Screener Option.

        :param symbols: Option symbols to subscribe to.
        '''
        await self._service_op(symbols, 'SCREENER_OPTION', 'SUBS', self.ScreenerFields)

    async def screener_option_unsubs(self, symbols):
        '''
        Un-Subscribe to Screener Option.

        :param symbols: Option symbols to unsubscribe from.
        '''
        await self._service_op(symbols, 'SCREENER_OPTION', 'UNSUBS')

    async def screener_option_add(self, symbols):
        '''
        Add symbols to the Screener Option list.

        :param symbols: Option symbols to add to the subscription.
        '''
        await self._service_op(symbols, 'SCREENER_OPTION', 'ADD', self.ScreenerFields)

    def add_screener_option_handler(self, handler):
        '''
        Register a function to handle Screener Option data as it is
        updated See :ref:`registering_handlers` for details.
        '''
        self._add_handler('SCREENER_OPTION',
            _Handler(handler, self.ScreenerFields))
