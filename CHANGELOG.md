# Changelog

Notable changes to this fork are summarized below. For the complete history,
see the repository's commits and tags.

## 1.7.7 — 2026-11-12

- Refresh the README and update links to this repository.
- Use a more precise clock for order request pacing.
- Detach streaming parse errors from the active reader.
- Prevent requests from an old streaming connection from being sent on its
  replacement, and serialize concurrent logins to avoid leaking sockets.

## 1.7.6 — 2026-11-11

- Add streaming VIEW operations, batched requests, subscription tracking, and
  support for restoring subscriptions.
- Validate and split stream symbol requests, normalize field identifiers and
  disconnect errors, and improve streaming recovery and message handling.
- Add opt-in background token refresh and controlled use of valid tokens after
  a refresh failure.
- Support nonblocking order calls with synchronous clients, opt-in order error
  handling and pacing, and bounded polling for recent orders.
- Validate string order price precision and document how to reconcile uncertain
  order outcomes.
- Increase the minimum Authlib version and improve market-data debug logging.

## 1.7.5 — 2026-11-09

- Add opt-in retries for transient failures on idempotent GET requests.
- Add options for shared streaming messages and sequential handlers, and expose
  handler failures through a callback.
- Improve token persistence, callback server error reporting, and log redaction.
- Begin adding public API type annotations and include the typing marker.
- Correct API and streaming documentation and strengthen dependency checks.

## 1.7.4 — 2026-11-05

- Harden authentication, logging, streaming, and order workflows.
- Raise minimum dependency versions.

## 1.7.3 — 2026-10-16

- Harden token refresh and sensitive-data handling.
- Improve callback server startup on macOS and fix test and documentation
  builds.

## 1.7.2 — 2026-09-28

- Improve order price handling with Decimal support and strengthen order
  validation.
- Add stream buffer limits and improve stream shutdown and handler handling.
- Improve logging redaction and support asynchronous token writes.

Earlier releases and the original project's history are available in the
[repository history](https://github.com/yoshishima/TalkToChuck/commits/main/)
and [upstream repository](https://github.com/alexgolec/schwab-py).
