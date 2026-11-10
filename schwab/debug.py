import atexit
from collections import defaultdict, deque
import datetime
from decimal import Decimal
import enum
import inspect
import json
import logging
import re
import sys
from urllib.parse import urlsplit
import schwab


_BUG_REPORT_LOGGING_ACTIVE = 0
_DEFAULT_MAX_TRANSIENT_REDACTIONS = 10_000
_DEFAULT_MAX_PERSISTENT_PER_LABEL = 32
_DEFAULT_MAX_LOG_MESSAGES = 10_000
_DEFAULT_MAX_LOG_CHARS = 10 * 1024 * 1024

# Placeholders are written in two phases through characters from the
# supplementary private-use planes, so a short registered string can never be
# replaced again inside a placeholder that was already inserted.
_SENTINEL_BASE = 0xF0000
_SENTINEL_LIMIT = 0x10FFFD - _SENTINEL_BASE
_SENTINEL_RE = re.compile('[\U000F0000-\U0010FFFF]')

# Market-data words that contain "id" without being identifiers, such as
# "bidPrice" or "midPrice". Removed before matching sensitive key patterns.
_NON_IDENTIFIER_WORDS = ('bid', 'mid')


def _build_secret_matcher(entries):
    # Aho-Corasick: terminal links avoid copying all suffix outputs into nodes.
    transitions = [{}]
    failures = [0]
    terminals = [None]
    output_links = [0]
    for index, (secret, _) in enumerate(entries):
        node = 0
        for char in secret:
            child = transitions[node].get(char)
            if child is None:
                child = len(transitions)
                transitions[node][char] = child
                transitions.append({})
                failures.append(0)
                terminals.append(None)
                output_links.append(0)
            node = child
        terminals[node] = index
    pending = deque(transitions[0].values())
    while pending:
        node = pending.popleft()
        for char, child in transitions[node].items():
            pending.append(child)
            fallback = failures[node]
            while fallback and char not in transitions[fallback]:
                fallback = failures[fallback]
            failures[child] = transitions[fallback].get(char, 0)
            target = failures[child]
            output_links[child] = target if terminals[target] is not None else output_links[target]
    return transitions, failures, terminals, output_links


def _matching_secret_indices(matcher, message):
    transitions, failures, terminals, output_links = matcher
    found = set()
    node = 0
    for char in message:
        while node and char not in transitions[node]:
            node = failures[node]
        node = transitions[node].get(char, 0)
        output = node
        while output:
            if terminals[output] is not None:
                found.add(terminals[output])
            output = output_links[output]
    return found


def get_logger():
    return get_redacted_logger(__name__)


class _LazyRedactedMessage:
    def __init__(self, msg, args):
        self.msg = msg
        self.args = args
        self.rendered = None

    def __str__(self):
        if self.rendered is not None:
            return self.rendered
        try:
            message = str(self.msg)
            if self.args:
                message = message % self.args
        except Exception as e:
            # Logging must never change program behaviour. Drop the
            # unformattable arguments rather than emitting them unredacted.
            message = '<unformattable log record: {}>'.format(type(e).__name__)
        self.rendered = schwab.LOG_REDACTOR.redact(message)
        self.msg = None
        self.args = None
        return self.rendered


class _RedactingFilter(logging.Filter):
    def filter(self, record):
        record.msg = _LazyRedactedMessage(record.msg, record.args)
        record.args = ()
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(
                record.exc_info)
        if record.exc_text:
            record.exc_text = schwab.LOG_REDACTOR.redact(record.exc_text)
        if record.stack_info:
            record.stack_info = schwab.LOG_REDACTOR.redact(record.stack_info)
        return True


_REDACTING_FILTER = _RedactingFilter()


class _SchwabHttpFilter(logging.Filter):
    '''Hide Schwab URLs in httpx's shared request logger.'''
    def filter(self, record):
        if (record.msg == 'HTTP Request: %s %s "%s %d %s"'
                and isinstance(record.args, tuple) and len(record.args) == 5):
            args = list(record.args)
            try:
                parsed = urlsplit(str(args[1]))
                if parsed.hostname and parsed.hostname.lower() == 'api.schwabapi.com':
                    args[1] = 'https://api.schwabapi.com/<redacted>'
                    record.args = tuple(args)
            except (ValueError, TypeError):
                # A malformed URL must not escape via diagnostic logging.
                args[1] = '<url omitted>'
                record.args = tuple(args)
        return True


class _WebsocketFrameFilter(logging.Filter):
    def filter(self, record):
        if record.msg in ('< %s', '> %s'):
            record.msg = 'WebSocket frame omitted'
            record.args = ()
        return True


_HTTP_FILTER = _SchwabHttpFilter()
_WEBSOCKET_FILTER = _WebsocketFrameFilter()
logging.getLogger('httpx2').addFilter(_HTTP_FILTER)


def get_websocket_logger():
    logger = get_redacted_logger('schwab.websocket')
    logger.addFilter(_WEBSOCKET_FILTER)
    # Frame omission must run before the general filter renders the record.
    logger.removeFilter(_REDACTING_FILTER)
    logger.addFilter(_REDACTING_FILTER)
    return logger


def get_redacted_logger(name):
    logger = logging.getLogger(name)
    logger.addFilter(_REDACTING_FILTER)
    return logger


def redact_json(payload):
    '''Sanitize structured payloads without retaining their values globally.

    Never raises: payloads that cannot be rendered, such as cyclic ones, are
    replaced by a short marker so that logging cannot change behaviour.'''
    try:
        return _redact_json(payload)
    except (TypeError, ValueError, RecursionError) as e:
        return '<unloggable payload: {}>'.format(type(e).__name__)


def _redact_json(payload):
    redactor = LogRedactor()
    register_redactions(payload, redactor=redactor)
    # Redact values before serialization as well as afterwards, so escaped
    # quotes, backslashes and Unicode cannot bypass string replacement.
    def sanitize(value):
        if isinstance(value, dict):
            return {key: sanitize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [sanitize(item) for item in value]
        if (str(value) in redactor.redacted_strings
                or str(value) in schwab.LOG_REDACTOR.redacted_strings):
            return schwab.LOG_REDACTOR.redact(redactor.redact(str(value)))
        if isinstance(value, str):
            return schwab.LOG_REDACTOR.redact(redactor.redact(value))
        return value
    text = json.dumps(sanitize(payload), default=_log_default)
    return schwab.LOG_REDACTOR.redact(text)


def _log_default(value):
    '''Render values json.dumps cannot serialize. Rendered values are strings,
    so they still pass through string redaction.'''
    if isinstance(value, (Decimal, datetime.date, datetime.time,
                          datetime.timedelta)):
        return str(value)
    if isinstance(value, enum.Enum):
        return str(value.value)
    return '<{}>'.format(type(value).__name__)


class LogRedactor:
    '''
    Collects strings that should not be emitted and replaces them with safe
    placeholders.
    '''

    def __init__(self, *, max_transient_entries=_DEFAULT_MAX_TRANSIENT_REDACTIONS,
                 max_persistent_per_label=_DEFAULT_MAX_PERSISTENT_PER_LABEL):
        self.redacted_strings = {}
        self.label_counts = defaultdict(int)
        self.max_transient_entries = max_transient_entries
        self.max_persistent_per_label = max_persistent_per_label
        self._persistent_strings = set()
        # Persistent strings per label, oldest first. Each token refresh
        # registers new tokens under the same labels, so only the most recent
        # values are kept; superseded tokens have expired.
        self._persistent_by_label = defaultdict(deque)
        self._sorted_redactions = None
        self._secret_matcher = None
        self._matcher_safe = False
        self._transient_limit_reached = False

    def register(self, string, label, *, persistent=True):
        '''
        Registers a string that should not be emitted and the label with with
        which it should be replaced.
        '''
        if string is None:
            return True
        string = str(string)
        if not string:
            return True
        if string in self.redacted_strings:
            if persistent and string not in self._persistent_strings:
                self._remember_persistent(
                        string, self.redacted_strings[string][0])
            return True

        if (not persistent and
                len(self.redacted_strings) - len(self._persistent_strings) >=
                self.max_transient_entries):
            self._transient_limit_reached = True
            return False

        self.label_counts[label] += 1
        self.redacted_strings[string] = (label, self.label_counts[label])
        if persistent:
            self._remember_persistent(string, label)
        self._sorted_redactions = None
        return True

    def _remember_persistent(self, string, label):
        self._persistent_strings.add(string)
        history = self._persistent_by_label[label]
        history.append(string)
        while len(history) > self.max_persistent_per_label:
            evicted = history.popleft()
            self._persistent_strings.discard(evicted)
            self.redacted_strings.pop(evicted, None)
            self._sorted_redactions = None

    @property
    def transient_limit_reached(self):
        return self._transient_limit_reached

    def clear_transient(self):
        self.redacted_strings = {
            string: label
            for string, label in self.redacted_strings.items()
            if string in self._persistent_strings
        }
        self.label_counts = defaultdict(int)
        for label, count in self.redacted_strings.values():
            self.label_counts[label] = max(self.label_counts[label], count)
        self._sorted_redactions = None
        self._transient_limit_reached = False

    def redact(self, msg):
        '''
        Scans the string for secret strings and returns a sanitized version with
        the secrets replaced with placeholders.
        '''
        if self._sorted_redactions is None:
            self._sorted_redactions = sorted(
                    self.redacted_strings.items(),
                    key=lambda item: -len(item[0]))
            self._secret_matcher = None
            self._matcher_safe = not any(
                _SENTINEL_RE.search(secret) for secret, _ in self._sorted_redactions)
        if not self._sorted_redactions:
            return msg
        if (len(self._sorted_redactions) > _SENTINEL_LIMIT
                or _SENTINEL_RE.search(msg) is not None):
            return self._redact_in_place(msg)

        # Phase 1 swaps each present string for a unique sentinel character,
        # longest first, so shorter strings cannot match inside longer ones.
        # Phase 2 swaps sentinels for placeholders, which are never rescanned.
        entries = self._sorted_redactions
        if len(entries) >= 100 and self._matcher_safe:
            if self._secret_matcher is None:
                self._secret_matcher = _build_secret_matcher(entries)
            indices = _matching_secret_indices(self._secret_matcher, msg)
            entries = [entries[index] for index in sorted(indices)]
        placeholders = []
        for string, (label, count) in entries:
            if string not in msg:
                continue
            msg = msg.replace(string, chr(_SENTINEL_BASE + len(placeholders)))
            placeholders.append(self._placeholder(label, count))
        for index, placeholder in enumerate(placeholders):
            msg = msg.replace(chr(_SENTINEL_BASE + index), placeholder)
        return msg

    def _placeholder(self, label, count):
        return '<REDACTED {}{}>'.format(
            label, '-{}'.format(count) if self.label_counts[label] > 1 else '')

    def _redact_in_place(self, msg):
        # Fallback for messages that already contain private-use characters.
        for string, (label, count) in self._sorted_redactions:
            if string in msg:
                msg = msg.replace(string, self._placeholder(label, count))
        return msg


def register_redactions_from_response(resp, *, return_sanitized=False):
    '''
    Register sensitive values from a response before its body is logged.

    Both successful and error responses are inspected because Schwab may echo
    account identifiers in an error payload. With ``return_sanitized``, return
    the sanitized JSON text (or an omission marker for non-JSON responses).
    Return None when capture is disabled and False when its limit is reached.
    '''
    if not _BUG_REPORT_LOGGING_ACTIVE:
        return None
    if schwab.LOG_REDACTOR.transient_limit_reached:
        return False

    try:
        payload = resp.json()
        if inspect.isawaitable(payload):
            close = getattr(payload, 'close', None)
            if close is not None:
                close()
            return '<response body omitted>' if return_sanitized else True
        success = register_redactions(payload, persistent=False)
        if return_sanitized and success:
            return redact_json(payload)
        return success
    except (json.decoder.JSONDecodeError, UnicodeDecodeError):
        return '<non-JSON response body omitted>' if return_sanitized else True


def register_redactions(obj, key_path=None,
                        bad_patterns=(
                            'auth', 'acl', 'displayname', 'id', 'key', 'token',
                            'accountnumber', 'hashvalue', 'firstname',
                            'lastname', 'nickname'),
                        whitelisted=frozenset((
                            'requestid',
                            'token_type',
                            'legid',
                            'bidid',
                            'askid',
                            'lastid',
                            'bidsizeinlong',
                            'bidsizeindouble',
                            'bidpriceindouble')),
                        *, persistent=True, redactor=None):
    '''
    Recursively iterates through the leaf elements of ``obj`` and registers
    elements with keys or ancestor keys matching a blacklist with the supplied
    ``Redactor`` (or the global redactor by default). A whitelisted field does
    not override a sensitive ancestor.
    '''
    if key_path is None:
        key_path = []
    if redactor is None:
        redactor = schwab.LOG_REDACTOR

    sensitive_names = {}

    def sensitive_key(key):
        name = str(key).lower()
        if name.isdecimal():
            return False
        if name not in sensitive_names:
            sensitive_names[name] = _is_sensitive_name(
                name, bad_patterns, whitelisted)
        return sensitive_names[name]

    def visit(value, sensitive):
        if isinstance(value, (list, dict)):
            items = enumerate(value) if isinstance(value, list) else value.items()
            for key, child in items:
                key_path.append(str(key) if isinstance(value, list) else key)
                try:
                    if not visit(child, sensitive or sensitive_key(key)):
                        return False
                finally:
                    key_path.pop()
            return True
        # Ignore booleans and single-character values: they are too broad to
        # register safely as secrets.
        if (sensitive and key_path and not isinstance(value, bool)
                and len(str(value)) > 1):
            return redactor.register(
                value, '-'.join(map(str, key_path)), persistent=persistent)
        return True

    return visit(obj, any(sensitive_key(key) for key in key_path))


def _is_sensitive_name(name, bad_patterns, whitelisted):
    if name in whitelisted:
        return False
    for word in _NON_IDENTIFIER_WORDS:
        name = name.replace(word, '')
    return any(bad in name for bad in bad_patterns)


def enable_bug_report_logging():
    '''
    Turns on bug report logging. Will collect all logged output, redact out
    anything that should be kept secret, and emit the result at program exit.

    Notes:
     * This method does a best effort redaction. Never share its output
       without verifying that all secret information is properly redacted.
     * Because this function records all logged output, it has a performance
       penalty. It should not be called in production code.
     * Capture is bounded. If the limits are reached, older messages are
       discarded and response bodies that cannot be safely redacted are
       omitted.
    '''
    _enable_bug_report_logging()


def _enable_bug_report_logging(
        output=sys.stderr, loggers=None,
        max_log_messages=_DEFAULT_MAX_LOG_MESSAGES,
        max_log_chars=_DEFAULT_MAX_LOG_CHARS):
    '''
    Module-internal version of :func:`enable_bug_report_logging`, intended for
    use in tests.
    '''
    if loggers is None:
        loggers = (
            schwab.auth.get_logger(),
            schwab.client.base.get_logger(),
            schwab.streaming.get_logger(),
            get_logger())

    class RecordingHandler(logging.Handler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.messages = deque()
            self.total_chars = 0
            self.dropped_messages = 0

        def emit(self, record):
            message = self.format(record)
            if len(message) > max_log_chars:
                message = message[:max_log_chars]

            while self.messages and (
                    len(self.messages) >= max_log_messages or
                    self.total_chars + len(message) > max_log_chars):
                removed = self.messages.popleft()
                self.total_chars -= len(removed)
                self.dropped_messages += 1

            if max_log_messages <= 0 or max_log_chars <= 0:
                self.dropped_messages += 1
                return

            self.messages.append(message)
            self.total_chars += len(message)

    handler = RecordingHandler()
    handler.setFormatter(logging.Formatter(
        '[%(filename)s:%(lineno)s:%(funcName)s] %(message)s'))

    logger_levels = {logger: logger.level for logger in loggers}
    for logger in loggers:
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)

    global _BUG_REPORT_LOGGING_ACTIVE
    _BUG_REPORT_LOGGING_ACTIVE += 1

    logs_written = False

    def write_logs():
        nonlocal logs_written
        if logs_written:
            return
        logs_written = True

        try:
            for logger in loggers:
                logger.removeHandler(handler)
                logger.setLevel(logger_levels[logger])

            if getattr(output, 'closed', False):
                return

            print(file=output)
            print(' ### BEGIN REDACTED LOGS ###', file=output)
            print(file=output)

            if handler.dropped_messages:
                print('<{} older log messages omitted>'.format(
                        handler.dropped_messages), file=output)
            for msg in handler.messages:
                msg = schwab.LOG_REDACTOR.redact(msg)
                print(msg, file=output)
        except BrokenPipeError:
            # Bug-report output is best-effort, especially from an atexit
            # callback whose output may be connected to a closed pipe.
            pass
        finally:
            global _BUG_REPORT_LOGGING_ACTIVE
            _BUG_REPORT_LOGGING_ACTIVE -= 1
            if not _BUG_REPORT_LOGGING_ACTIVE:
                schwab.LOG_REDACTOR.clear_transient()
    atexit.register(write_logs)

    get_logger().debug('schwab-api version %s', schwab.__version__)

    return write_logs
