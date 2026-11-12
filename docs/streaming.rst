.. highlight:: python
.. py:module:: schwab.streaming

.. _stream:


================
Streaming Client
================

A wrapper around the Schwab streaming data API.  This API is a websockets-based 
streaming API that provides to up-to-the-second data on market activity. Most 
impressively, it provides realtime data, including Level Two and time of sale 
data for major equities, options, and futures exchanges. 

Here's an example of how you can receive book snapshots of ``GOOG`` (note if you 
run this outside regular trading hours you may not see anything):

.. code-block:: python

  from schwab.auth import easy_client
  from schwab.client import Client
  from schwab.streaming import StreamClient

  import asyncio
  import json

  # Assumes you've already created a token. See the authentication page for more
  # information.
  client = easy_client(
          api_key='YOUR_API_KEY',
          app_secret='YOUR_APP_SECRET',
          callback_url='https://127.0.0.1',
          token_path='/path/to/token.json')
  stream_client = StreamClient(client)

  async def read_stream():
      await stream_client.login()
 
      def print_message(message):
        print(json.dumps(message, indent=4))

      # Always add handlers before subscribing because many streams start sending 
      # data immediately after success, and messages with no handlers are dropped.
      stream_client.add_nasdaq_book_handler(print_message)
      await stream_client.nasdaq_book_subs(['GOOG'])

      while True:
          await stream_client.handle_message()

  asyncio.run(read_stream())

The streaming connection is configured from the ``streamerInfo`` returned by
Schwab's user-preferences endpoint; it cannot be scoped by passing an account
number. The legacy ``account_id`` constructor argument is deprecated and has no
effect.


++++++++++++
Use Overview
++++++++++++

The example above demonstrates the end-to-end workflow for using ``schwab.stream``. 
There's more in there than meets the eye, so let's dive into the details.


----------
Logging In
----------

Before we can perform any stream operations, the client must be logged in to the 
stream. Unlike the HTTP client, in which every request is authenticated using a 
token, this client sends unauthenticated requests and instead authenticates the 
entire stream. As a result, this login process is distinct from the token 
generation step that's used in the HTTP client.

Stream login is accomplished simply by calling :meth:`StreamClient.login()`. Once
this happens successfully, all stream operations can be performed. Attemping to
perform operations that require login before this function is called raises an
exception.

Login retrieves user preferences through the supplied HTTP client. An async HTTP
client avoids blocking the event loop. For a synchronous client, login runs that
lookup on the event-loop thread by default and emits a warning. Applications
whose synchronous client, session and token callbacks are safe to call from a
worker thread may instead use
``StreamClient(client, offload_sync_preferences=True)``. Canceling login does not
stop a synchronous request that is already running in that worker thread.

.. automethod:: schwab.streaming.StreamClient.login

Incoming WebSocket messages default to a maximum of 4 MiB. Override this limit
using, for example, ``await stream.login(websocket_connect_args={'max_size':
8 * 1024 * 1024})``. A larger limit can increase memory use; ``None`` disables
the limit. The limit applies to a complete message, including fragmented ones.


-----------
Logging Out
-----------

For a clean exit, it's recommended to log out of the stream when you're done.

.. automethod:: schwab.streaming.StreamClient.logout


----------------------
Subscribing to Streams
----------------------

These functions have names that follow the pattern ``SERVICE_NAME_subs``. These 
functions send a request to enable streaming data for a particular data stream. 
Calls on the same service are serialized; calls on different services may run
concurrently on the client's event loop. The client is not thread safe.

When subscriptions are called multiple times on the same stream, the results 
vary. What's more, these results aren't documented in the official 
documentation. As a result, it's recommended not to call a subscription function 
more than once for any given stream.

Some services, notably :ref:`equity_charts` and :ref:`futures_charts`, 
offer ``SERVICE_NAME_add`` functions which can be used to add symbols to the 
stream after the subscription has been created. For others, calling the 
subscription methods again seems to clear the old subscription and create a new 
one. Note this behavior is not officially documented, so this interpretation may 
be incorrect.


----------------------
Add symbols to Streams
----------------------

These functions have names that follow the pattern ``SERVICE_NAME_add``.
These functions send a request to add to the list of subscribed symbols for a
particular data stream.

For level-one services, omitting ``fields`` reuses the fields from the last
acknowledged ``SUBS`` or ``VIEW`` for that service. If there is no such request,
all fields are requested. Pass ``fields`` explicitly to select a different set.
Schwab's treatment of different field sets for the same service is not verified
here; a ``VIEW`` of only some symbols still sets the default for later ``ADD``
requests on that service. Use consistent fields if your application depends on
uniform updates.

Symbol lists must be nonempty. Duplicate symbols are removed, and large lists
are split into requests of at most 500 symbols by default. Set the positive
``max_symbols_per_request`` constructor argument to change this client-side
limit. A split subscription sends ``SUBS`` followed by ``ADD`` requests; if a
later request fails, earlier acknowledged chunks remain subscribed.


-------------------------
Un-Subscribing to Streams
-------------------------

These functions have names that follow the pattern ``SERVICE_NAME_unsubs``.
These functions send a request to disable the symbols of a streaming data for a
particular data stream. When unsubscribing to services with symbols, symbols which
were not explicitly unsubscribed remain subscribed.

The client tracks successfully acknowledged subscriptions and removals. After
an unexpected disconnect, call ``login()`` and then ``resubscribe()`` to replay
that state. This does not reconnect automatically or recover data missed while
disconnected. A timeout or cancellation can leave server state uncertain even
though no acknowledgement was recorded; reconcile application state as needed.
Restoration failures leave the registry available for retry.

``await stream.view(service, symbols, fields=fields)`` sends ``VIEW`` for a
level-one service and records acknowledged field changes for restoration.
``await stream.send_requests(requests)`` sends multiple low-level request
objects in one message; each object contains ``service``, ``command`` and
``parameters``. Low-level requests do not update the subscription registry, so
applications using them for subscription changes must manage restoration. A
batch can partially succeed; its acknowledgements are validated individually.


--------------------
Registering Handlers
--------------------

By themselves, the subscription functions outlined above do nothing except cause 
messages to be sent to the client. The ``add_SERVICE_NAME_handler`` functions 
register functions that will receive these messages when they arrive. When 
messages arrive, these handlers will be called serially. There is no limit to 
the number of handlers that can be registered to a service.


.. _registering_handlers:

-----------------
Handling Messages
-----------------

Once the stream client is properly logged in, subscribed to streams, and has 
handlers registered, we can start handling messages. This is done simply by 
awaiting on the ``handle_message()`` function. This function reads a single 
message and dispatches it to the appropriate handler or handlers.

If a message is received for which no handler is registered, that message is 
ignored.

Each handler receives its own copy of the message, including nested content.
Changes made by one handler do not affect other handlers. Messages decoded by
the default decoder are copied with a fast copier for plain JSON data. Values
of other types produced by a custom decoder, such as ``Decimal`` or
``OrderedDict``, are copied with :func:`copy.deepcopy`. Objects that a custom
decoder shares between several places in one message become separate copies.

Applications that treat handler messages as read-only may pass
``copy_messages=False`` to share each relabeled message between handlers and
avoid per-handler copies. Mutating a shared message can affect other handlers
and a dispatch resumed after cancellation. The default preserves isolation.

Asynchronous handlers run in background tasks. By default, a ``StreamClient``
allows at most 100 pending handler tasks across all services. Set the positive
integer ``max_pending_handler_tasks`` constructor argument to adjust this limit.
Exceptions from synchronous and asynchronous handlers are logged at ERROR and
do not propagate from ``handle_message()``. Pass a synchronous
``on_handler_error(exception)`` callback to ``StreamClient`` to notify your
application. The callback runs on the event-loop thread; its own exceptions are
logged. It should return promptly and schedule any asynchronous work itself.
When all slots are occupied, ``handle_message()`` waits for a handler to finish
before invoking the next handler. Keep one message-consumption loop so this
backpressure also limits how quickly your application reads from the socket.
Slow handlers can still delay updates; handlers should finish promptly and use
timeouts for external I/O.

Large ordinary market-data entries yield periodically during relabeling, and
dispatch yields between groups of top-level entries. JSON decoding, individual
large values, and specialized book relabeling still run synchronously.
Market-data DEBUG records contain a count rather than the full payload by default.
Set ``log_stream_payloads=True`` to include redacted payloads for diagnostics;
rendering those records can be expensive.

If a capacity wait is canceled, the next ``handle_message()`` call resumes the
message at its next uncalled handler. Handlers already invoked are not repeated.
Restored messages are dispatched before newer queued messages, including while
a subscription is waiting for its acknowledgement. Concurrent calls to
``handle_message()`` serialize message delivery and handler dispatch to preserve
this order. Asynchronous handler completion order remains independent.

To process asynchronous handlers for a service sequentially, pass for example
``sequential_handler_services={'ACCT_ACTIVITY'}`` to ``StreamClient``. Tasks
for that service acquire a shared lock in dispatch order; other services remain
independent. The pending-task limit still applies, so slow handlers impose
backpressure. Synchronous handlers must themselves complete promptly.
Use coroutine functions for sequential handlers: a handler that starts a task
itself before returning it can perform side effects before the lock is acquired.

Logout or reconnect causes pending message consumers and subscription requests
from the previous connection to raise ``schwab.streaming.StreamClosedError``,
a subclass of ``ConnectionError``. This includes consumers waiting for dispatch
capacity or behind another consumer, and requests waiting for a send or service
lock. Concurrent calls to ``login()`` are serialized; each successful login
replaces the previous connection. Explicitly canceling a consumer task still
raises ``asyncio.CancelledError``.
Unexpected WebSocket closure also raises ``StreamClosedError``; its cause holds
the underlying WebSocket exception. After closure, log in again before use.

Logout and reconnect cancel pending handler tasks from the previous connection.
Handlers should release their resources when canceled and propagate
``asyncio.CancelledError``. A handler may itself call ``logout()``; that handler
is allowed to finish. Each handler runs independently, so completion order is
not guaranteed for asynchronous handlers.


Handler registrations survive ``logout()`` and ``login()``. Adding the same
callable to the same service again has no effect, including repeated access to
a bound method. Newly created lambdas or callable instances are distinct handlers.
Use ``clear_handlers()`` to remove all registrations, or
``clear_handlers('ACCT_ACTIVITY')`` to clear one wire service. Clearing affects
future dispatch snapshots; it does not cancel running handlers or change a
message whose dispatch has already started.

.. automethod:: schwab.streaming.StreamClient.clear_handlers

Handlers should take a single argument representing the stream message received:

.. code-block:: python

  import json

  def sample_handler(msg):
      print(json.dumps(msg, indent=4))


---------------------
Data Field Relabeling
---------------------

Under the hood, this API returns JSON objects with numerical key representing
labels: 

.. code-block:: python

  {
    "service": "CHART_EQUITY",
    "timestamp": 1715908546054,
    "command": "SUBS",
    "content": [{
          "seq": 0,
          "key": "MSFT",
          "1": 779,
          "2": 421.65,
          "3": 421.79,
          "4": 421.65,
          "5": 421.755,
          "6": 26.0,
          "7": 1715903940000,
          "8": 19859
      }]
  }

These labels are tricky to decode, and require a knowledge of the documentation 
to decode properly. ``schwab-api`` makes your life easier by doing this decoding 
for you, replacing numerical labels with strings proposed by the community For 
instance, the message above would be relabeled as:

.. code-block:: python

  {
    "service": "CHART_EQUITY",
    "timestamp": 1715908546054,
    "command": "SUBS",
    "content": [{
          "seq": 0,
          "key": "MSFT",
          "SEQUENCE": 779,
          "OPEN_PRICE": 421.65,
          "HIGH_PRICE": 421.79,
          "LOW_PRICE": 421.65,
          "CLOSE_PRICE": 421.755,
          "VOLUME": 26.0,
          "CHART_TIME_MILLIS": 1715903940000,
          "CHART_DAY": 19859
      }]
  }

This documentation describes the various fields and their numerical values. You 
can find them by investigating the various enum classes ending in ``***Fields``.

Some streams, such as the ones described in :ref:`level_one`, allow you to
specify a subset of fields to be returned. Subscription handlers for these
services take a list of the appropriate field enums the extra ``fields``
parameter. If nothing is passed to this parameter, all supported fields are
requested. An empty list raises ``ValueError``; pass ``None`` to request all
fields. Duplicate fields are requested once.


---------------
Stream Statuses
---------------

Schwab's streaming functionality is very similar to that of the former 
TDAmeritrade. So similar, in fact, that this module is largely a direct 
adaptation of the implementation developed for the older `tda-api library
<https://tda-api.readthedocs.io/en/latest/streaming.html>`__. 

As a result of this nearly direct copy-pasting, some streams may have been 
carried over which don't actually work. What's more, some streams *never* 
worked, even in ``tda-api``, but were only implemented because some old, 
now-defunct documentation referred to them.

The community is in the process of making sense of this new world. You are 
encouraged to try and use this streaming library and report what you find back 
to our `Discord server <https://discord.gg/BEr6y6Xqyv>`__. We'll be updating 
this page as we discover new things.

The following streams are confirmed working:
 * :ref:`charts`
 * :ref:`level_one`
 * :ref:`level_two`
 * :ref:`screener`
 * :ref:`account_activity`


.. _charts:

++++++++++++
OHLCV Charts
++++++++++++

These streams summarize trading activity on a minute-by-minute basis for 
equities and futures, providing OHLCV (Open/High/Low/Close/Volume) data.


.. _equity_charts:

-------------
Equity Charts
-------------

Minute-by-minute OHLCV data for equities.

.. automethod:: schwab.streaming::StreamClient.chart_equity_subs
.. automethod:: schwab.streaming::StreamClient.chart_equity_unsubs
.. automethod:: schwab.streaming::StreamClient.chart_equity_add
.. automethod:: schwab.streaming::StreamClient.add_chart_equity_handler
.. autoclass:: schwab.streaming::StreamClient.ChartEquityFields
  :members:
  :undoc-members:


.. _futures_charts:

--------------
Futures Charts
--------------

Minute-by-minute OHLCV data for futures.

.. automethod:: schwab.streaming::StreamClient.chart_futures_subs
.. automethod:: schwab.streaming::StreamClient.chart_futures_unsubs
.. automethod:: schwab.streaming::StreamClient.chart_futures_add
.. automethod:: schwab.streaming::StreamClient.add_chart_futures_handler
.. autoclass:: schwab.streaming::StreamClient.ChartFuturesFields
  :members:
  :undoc-members:


.. _level_one:

++++++++++++++++
Level One Quotes
++++++++++++++++

Level one quotes provide an up-to-date view of bid/ask/volume data. In 
particular they list the best available bid and ask prices, together with the 
requested volume of each. They are updated live as market conditions change.


.. _level_one_quotes_stream:

---------------
Equities Quotes
---------------

Level one quotes for equities traded on NYSE, AMEX, and PACIFIC.

.. automethod:: schwab.streaming::StreamClient.level_one_equity_subs
.. automethod:: schwab.streaming::StreamClient.level_one_equity_unsubs
.. automethod:: schwab.streaming::StreamClient.level_one_equity_add
.. automethod:: schwab.streaming::StreamClient.add_level_one_equity_handler
.. autoclass:: schwab.streaming::StreamClient.LevelOneEquityFields
  :members:
  :undoc-members:


.. _level_one_option_stream:

--------------
Options Quotes
--------------

Level one quotes for options. Note you can use 
:meth:`Client.get_option_chain() <schwab.client.Client.get_option_chain>` to fetch
available option symbols.

.. automethod:: schwab.streaming::StreamClient.level_one_option_subs
.. automethod:: schwab.streaming::StreamClient.level_one_option_unsubs
.. automethod:: schwab.streaming::StreamClient.level_one_option_add
.. automethod:: schwab.streaming::StreamClient.add_level_one_option_handler
.. autoclass:: schwab.streaming::StreamClient.LevelOneOptionFields
  :members:
  :undoc-members:


.. _level_one_futures_stream:

--------------
Futures Quotes
--------------

Level one quotes for futures.

.. automethod:: schwab.streaming::StreamClient.level_one_futures_subs
.. automethod:: schwab.streaming::StreamClient.level_one_futures_unsubs
.. automethod:: schwab.streaming::StreamClient.level_one_futures_add
.. automethod:: schwab.streaming::StreamClient.add_level_one_futures_handler
.. autoclass:: schwab.streaming::StreamClient.LevelOneFuturesFields
  :members:
  :undoc-members:


.. _level_one_futures_options_stream:

----------------------
Futures Options Quotes
----------------------

Level one quotes for futures options.

.. automethod:: schwab.streaming::StreamClient.level_one_futures_options_subs
.. automethod:: schwab.streaming::StreamClient.level_one_futures_options_unsubs
.. automethod:: schwab.streaming::StreamClient.level_one_futures_options_add
.. automethod:: schwab.streaming::StreamClient.add_level_one_futures_options_handler
.. autoclass:: schwab.streaming::StreamClient.LevelOneFuturesOptionsFields
  :members:
  :undoc-members:


.. _level_one_forex_stream:

------------
Forex Quotes
------------

Level one quotes for foreign exchange pairs.

.. automethod:: schwab.streaming::StreamClient.level_one_forex_subs
.. automethod:: schwab.streaming::StreamClient.level_one_forex_unsubs
.. automethod:: schwab.streaming::StreamClient.level_one_forex_add
.. automethod:: schwab.streaming::StreamClient.add_level_one_forex_handler
.. autoclass:: schwab.streaming::StreamClient.LevelOneForexFields
  :members:
  :undoc-members:


.. _level_two:

++++++++++++++++++++
Level Two Order Book 
++++++++++++++++++++

Level two streams provide a view on continuous order books of various securities.
The level two order book describes the current bids and asks on the market, and 
these streams provide snapshots of that state.

Due to the lack of official documentation, these streams are largely reverse 
engineered.  While the labeled data represents a best effort attempt to
interpret stream fields, it's possible that something is wrong or incorrectly
labeled.

The documentation lists more book types than are implemented here. In 
particular, it also lists ``FOREX_BOOK``, ``FUTURES_BOOK``, and
``FUTURES_OPTIONS_BOOK`` as accessible streams. All experimentation has resulted 
in these streams refusing to connect, typically returning errors about 
unavailable services. Due to this behavior and the lack of official 
documentation for book streams generally, ``schwab-api`` assumes these streams are not
actually implemented, and so excludes them. If you have any insight into using
them, please `let us know <https://github.com/alexgolec/schwab-api/issues>`__.


-------------------------------------
Equities Order Books: NYSE and NASDAQ
-------------------------------------

``schwab-api`` supports level two data for NYSE and NASDAQ, which are the two major 
exchanges dealing in equities, ETFs, etc. Stocks are typically listed on one or 
the other, and it is useful to learn about the differences between them:

 * `"The NYSE and NASDAQ: How They Work" on Investopedia
   <https://www.investopedia.com/articles/basics/03/103103.asp>`__
 * `"Here's the difference between the NASDAQ and NYSE" on Business Insider
   <https://www.businessinsider.com/
   heres-the-difference-between-the-nasdaq-and-nyse-2017-7>`__
 * `"Can Stocks Be Traded on More Than One Exchange?" on Investopedia
   <https://www.investopedia.com/ask/answers/05/stockmultipleexchanges.asp>`__

You can identify on which exchange a symbol is listed by using
:meth:`Client.search_instruments() <schwab.client.Client.search_instruments>`:

.. code-block:: python

  r = c.search_instruments(['GOOG'], projection=c.Instrument.Projection.FUNDAMENTAL)
  assert r.status_code == httpx.codes.OK, r.raise_for_status()
  print(r.json()['GOOG']['exchange'])  # Outputs NASDAQ

However, many symbols have order books available on these streams even though 
this API call returns neither NYSE nor NASDAQ. The only sure-fire way to find out
whether the order book is available is to attempt to subscribe and see what 
happens.

Note to preserve equivalence with what little documentation there is, the NYSE
book is called "listed." Testing indicates this stream corresponds to the NYSE
book, but if you find any behavior that suggests otherwise please
`let us know <https://github.com/alexgolec/schwab-api/issues>`__.

.. automethod:: schwab.streaming::StreamClient.nyse_book_subs
.. automethod:: schwab.streaming::StreamClient.nyse_book_unsubs
.. automethod:: schwab.streaming::StreamClient.nyse_book_add
.. automethod:: schwab.streaming::StreamClient.add_nyse_book_handler

.. automethod:: schwab.streaming::StreamClient.nasdaq_book_subs
.. automethod:: schwab.streaming::StreamClient.nasdaq_book_unsubs
.. automethod:: schwab.streaming::StreamClient.nasdaq_book_add
.. automethod:: schwab.streaming::StreamClient.add_nasdaq_book_handler


------------------
Options Order Book
------------------

This stream provides the order book for options. It's not entirely clear what 
exchange it aggregates from, but it's been tested to work and deliver data. The 
leading hypothesis is that it is the order book for the 
`Chicago Board of Exchange <https://www.cboe.com/us/options>`__ options 
exchanges, although this is an admittedly an uneducated guess.

.. automethod:: schwab.streaming::StreamClient.options_book_subs
.. automethod:: schwab.streaming::StreamClient.options_book_unsubs
.. automethod:: schwab.streaming::StreamClient.options_book_add
.. automethod:: schwab.streaming::StreamClient.add_options_book_handler


.. _screener:

++++++++
Screener
++++++++

Top 10 advances and decliners by volume, trades, percent change and average percent
volume.

Symbols in upper case and separated by commas.

(PREFIX)_(SORTFIELD)_(FREQUENCY) where PREFIX is:
 * Indices: $COMPX $DJI, $SPX.X, INDEX_ALL
 * Exchanges: NYSE, NASDAQ, OTCBB, EQUITY_ALL
 * Option: OPTION_PUT, OPTION_CALL, OPTION_ALL

and sortField is:
 * VOLUME, TRADES, PERCENT_CHANGE_UP, PERCENT_CHANGE_DOWN, AVERAGE_PERCENT_VOLUME

and frequency is:
 * 0, 1, 5, 10, 30 60 minutes (0 is for all day)

Both the equity and option screener streams use a common set of fields:

.. autoclass:: schwab.streaming::StreamClient.ScreenerFields
  :members:
  :undoc-members:


---------------
Screener Equity
---------------

.. automethod:: schwab.streaming::StreamClient.screener_equity_subs
.. automethod:: schwab.streaming::StreamClient.screener_equity_unsubs
.. automethod:: schwab.streaming::StreamClient.screener_equity_add
.. automethod:: schwab.streaming::StreamClient.add_screener_equity_handler


---------------
Screener Option
---------------

.. automethod:: schwab.streaming::StreamClient.screener_option_subs
.. automethod:: schwab.streaming::StreamClient.screener_option_unsubs
.. automethod:: schwab.streaming::StreamClient.screener_option_add
.. automethod:: schwab.streaming::StreamClient.add_screener_option_handler


.. _account_activity:

++++++++++++++++
Account Activity
++++++++++++++++

.. automethod:: schwab.streaming::StreamClient.account_activity_sub
.. automethod:: schwab.streaming::StreamClient.account_activity_unsubs
.. automethod:: schwab.streaming::StreamClient.add_account_activity_handler
.. autoclass:: schwab.streaming::StreamClient.AccountActivityFields
  :members:
  :undoc-members:


++++++++++++++++++++++++++++++++++++
Malformed messages and buffer limits
++++++++++++++++++++++++++++++++++++

Invalid JSON and invalid top-level stream envelopes are delivered as
``UnparsableMessage`` by ``handle_message()`` in message order. The exception's
``raw_msg`` attribute retains the original frame for diagnostics and may contain
sensitive data; the exception message does not include the frame. Malformed
messages do not fail pending subscription acknowledgements; the reader continues
processing the connection. A request with no valid acknowledgement still times
out according to ``response_timeout``. Data and notification entries without a
valid service are logged and skipped individually. Any exception raised by a
custom :class:`~schwab.streaming.StreamJsonDecoder` is reported the same way,
as ``UnparsableMessage`` with the original exception as its cause. Book
entries with missing or partial price levels are delivered with the parts that
are present relabeled.

``response_timeout`` must be ``None`` (wait indefinitely) or a positive, finite
number of seconds.

``StreamClient(max_pending_messages=1000)`` limits queued incoming messages,
including parse-error markers, while acknowledgements are outstanding. The
limit must be a positive integer. Handler-task backpressure is separate from
this buffer. Acknowledgements can still be read when the buffer is exactly full.
If another message would exceed the limit, pending callers receive
``StreamBufferOverflowError`` (a ``StreamClosedError``), the connection closes,
and buffered messages are discarded. Reconnect, resubscribe, and reconcile
application state after this error. A message already being dispatched is
additional to this limit and may be restored to the queue on cancellation.
