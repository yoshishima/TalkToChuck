.. highlight:: python
.. py:module:: schwab.client


.. _client:

===========
HTTP Client
===========

A naive, unopinionated wrapper around the
`Schwab individual trader API 
<https://developer.schwab.com/products/trader-api--individual>`_. This
client provides access to all endpoints of the API in as easy and direct a way 
as possible.


**Do not attempt to use more than one Client object per token file, as
this will likely cause issues with the underlying OAuth2 session management**

.. code-block:: python

  import httpx2 as httpx

  from schwab.auth import easy_client

  # Follow the instructions on the screen to authenticate your client.
  c = easy_client(
          api_key='APIKEY',
          app_secret='APP_SECRET',
          callback_url='https://127.0.0.1',
          token_path='/tmp/token.json')

  resp = c.get_price_history_every_day('AAPL')
  assert resp.status_code == httpx.codes.OK
  history = resp.json()

Note we we create a new client using the ``auth`` package as described in
:ref:`auth`. Creating a client directly is possible, but not recommended.

+++++++++++++++++++
Asyncio Support
+++++++++++++++++++

An asynchronous variant is available through a keyword to the client
constructor. This allows for higher-performance API usage, at the cost
of slightly increased application complexity.

.. code-block:: python

  import httpx2 as httpx

  from schwab.auth import easy_client

  async def main():
      c = easy_client(
              api_key='APIKEY',
              app_secret='APP_SECRET',
              callback_url='https://localhost',
              token_path='/tmp/token.json',
              asyncio=True)

      resp = await c.get_price_history_every_day('AAPL')
      assert resp.status_code == httpx.codes.OK
      history = resp.json()

  if __name__ == '__main__':
      import asyncio
      asyncio.run(main())

+++++++++++++++++++
Calling Conventions
+++++++++++++++++++

Function parameters are categorized as either required or optional.  Required 
parameters are passed as positional arguments.  Optional parameters, are passed 
as keyword arguments. 

Account hashes, order IDs, and transaction IDs used in URL paths must be
nonempty and contain only ASCII letters, digits, underscores, or hyphens.
Malformed IDs raise ``ValueError`` before a request is sent. Pass symbols and
other path values in their original form; the client percent-encodes them as
individual path segments. Empty values and the dot segments ``.`` and ``..``
are rejected.

Parameters which have special values recognized by the API are 
represented by `Python enums <https://docs.python.org/3/library/enum.html>`_. 
This is because the API rejects requests which pass unrecognized values, and 
this enum wrapping is provided as a convenient mechanism to avoid consternation 
caused by accidentally passing an unrecognized value.

By default, passing values other than the required enums will raise a
``ValueError``. If you believe the API accepts a value that isn't supported 
here, you can use ``set_enforce_enums`` to disable this behavior at your own 
risk. If you *do* find a supported value that isn't listed here, please open an
issue describing it or submit a PR adding the new functionality.


+++++++++++++
Return Values
+++++++++++++

All methods return a response object generated under the hood by the
`HTTPX2 <https://httpx2.pydantic.dev/quickstart/#response-content>`__ module.
For a full listing of what's possible, read that module's documentation. Most if
not all users can simply use the following pattern:

.. code-block:: python

  r = client.some_endpoint()
  assert r.status_code == httpx.codes.OK, r.raise_for_status()
  data = r.json()

The API indicates errors using the response status code, and this pattern will 
raise the appropriate exception if the response is not a success. The data can 
be fetched by calling the ``.json()`` method. 

This data will be pure python data structures which can be directly accessed. 
You can also use your favorite data analysis library's dataframe format using 
the appropriate library. For instance you can create a `pandas
<https://pandas.pydata.org/>`__ dataframe using `its conversion method 
<https://pandas.pydata.org/pandas-docs/stable/reference/api/
pandas.DataFrame.from_dict.html>`__.

**Note:** Because the author has no relationship whatsoever with Charles Schwab,
this document makes no effort to describe the structure of the returned JSON 
objects. Schwab might change them at any time, at which point this document will 
become silently out of date. Instead, each of the methods described below 
contains a link to the official documentation. For endpoints that return 
meaningful JSON objects, it includes a JSON schema which describes the return 
value. Please use that documentation or your own experimentation when figuring 
out how to use the data returned by this API.


.. _account_hashes:

++++++++++++++
Account Hashes
++++++++++++++

Many methods of this API are parametrized by account. However, the API does not 
accept raw account numbers, but rather account hashes. You can fetch these 
hashes using the ``get_account_numbers`` method :ref:`(link) 
<account_hashes_method>`. This method provides a list of raw account-number and
account-hash mappings. The hash must be passed when referring to an account in
API calls.

Here is an example of how to fetch an account hash and use it to place an order:

.. code-block:: python

  import httpx2 as httpx

  from schwab.auth import easy_client
  from schwab.orders.equities import equity_buy_market

  c = easy_client(
          token_path='/path/to/token.json',
          api_key='api-key',
          app_secret='app-secret',
          callback_url='https://callback.com')

  resp = c.get_account_numbers()
  assert resp.status_code == httpx.codes.OK

  # The response has the following structure. If you have multiple linked
  # accounts, you'll need to inspect this object to find the hash you want:
  # [
  #    {
  #        "accountNumber": "123456789",
  #        "hashValue":"123ABCXYZ"
  #    }
  #]
  account_hash = resp.json()[0]['hashValue']

  c.place_order(account_hash, equity_buy_market('AAPL', 1))



++++++++++++++++++
Timeout Management
++++++++++++++++++

Timeouts for HTTP calls are managed under the hood by the ``httpx2`` library.
``schwab-py`` defaults to 30 seconds, which experience has shown should be more 
than enough to allow even the slowest API calls to complete. A different timeout 
specification can be set using this method:

.. automethod:: schwab.client.Client.set_timeout


++++++++++++++++++++++++
Closing the HTTP Session
++++++++++++++++++++++++

Clients keep a pool of HTTP connections open. Close it when you are done with a
client, for example before discarding it in a long-running process:

.. automethod:: schwab.client.Client.close_session
.. automethod:: schwab.client.AsyncClient.close_async_session


+++++++++++++
Thread Safety
+++++++++++++

A synchronous client may be shared by several threads. Token refreshes are
serialized: when an expiring token is used by several threads at once, one
thread refreshes it and the others wait and reuse the new token. Custom
``token_write_func`` callbacks are therefore called once per refresh, but they
run on whichever thread refreshed. Separate clients for the same token still do
not coordinate; see :ref:`token_expiration` and the token-file notes in the
authentication documentation.


+++++++++
Token Age
+++++++++

.. automethod:: schwab.client.Client.token_age


++++++++++++
Account Info
++++++++++++

These methods provide access to useful information about accounts. An incomplete 
list of the most interesting bits:

* Account balances, including available trading balance
* Positions
* Order history

See the official documentation for each method for a complete response schema.

.. _account_hashes_method:

.. automethod:: schwab.client.Client.get_account_numbers
.. automethod:: schwab.client.Client.get_account
.. automethod:: schwab.client.Client.get_accounts
.. autoclass:: schwab.client.Client.Account
  :members:
  :undoc-members:


+++++++++++++
Price History
+++++++++++++

Schwab provides price history for equities and ETFs. It does not provide price 
history for options, futures, or any other instruments. 

The raw :meth:`Client.get_price_history` endpoint supports either a period or an
explicit start and end date. The convenience methods below always use an
explicit date range: a missing start date defaults to January 1, 1971 UTC, and a
missing end date defaults to the current time. They therefore omit the
``period`` query parameter. Schwab may still limit the returned history based on
the requested candle frequency.

Use the convenience methods to fetch candles by minute, day, or week. Supply
``start_datetime`` and ``end_datetime`` when you need narrower bounds.


.. automethod:: schwab.client.Client.get_price_history_every_minute
.. automethod:: schwab.client.Client.get_price_history_every_five_minutes
.. automethod:: schwab.client.Client.get_price_history_every_ten_minutes
.. automethod:: schwab.client.Client.get_price_history_every_fifteen_minutes
.. automethod:: schwab.client.Client.get_price_history_every_thirty_minutes
.. automethod:: schwab.client.Client.get_price_history_every_day
.. automethod:: schwab.client.Client.get_price_history_every_week

For the sake of completeness, here is the documentation for the raw price 
history endpoint, in all its complexity.

.. automethod:: schwab.client.Client.get_price_history
.. autoclass:: schwab.client.Client.PriceHistory
  :members:
  :undoc-members:
  :member-order: bysource

.. _orders-section:

++++++++++++++
Current Quotes
++++++++++++++

.. automethod:: schwab.client.Client.get_quote
.. automethod:: schwab.client.Client.get_quotes

.. _option_chain:

+++++++++++++
Option Chains
+++++++++++++

Unfortunately, option chains are well beyond the ability of your humble author. 
You are encouraged to read the official API documentation to learn more.

If you *are* knowledgeable enough to write something more substantive here, 
please follow the instructions in :ref:`contributing` to send in a patch.

.. automethod:: schwab.client.Client.get_option_chain
.. autoclass:: schwab.client.Client.Options
  :members:
  :undoc-members:

+++++++++++++++++++++++++++++++++++++
Instrument Searching and Fundamentals
+++++++++++++++++++++++++++++++++++++

.. automethod:: schwab.client.Client.get_instruments
.. automethod:: schwab.client.Client.get_instrument_by_cusip
.. autoclass:: schwab.client.Client.Instrument
  :members:
  :undoc-members:

++++++
Orders
++++++


.. _placing_new_orders:

------------------
Placing New Orders
------------------

Placing new orders can be a complicated task. The :meth:`Client.place_order` 
method is used to create all orders, from equities to options. The precise order 
type is defined by a complex order spec. Schwab provides some `example order 
specs`_ to illustrate the process and provides a schema in the `place order 
documentation 
<https://developer.schwab.com/products/trader-api--individual/details/specifications/Retail%20Trader%20API%20Production>`__, 
but beyond that we're on our own.

``schwab-api`` includes some helpers, described in :ref:`order_templates`, which 
provide an incomplete utility for creating various order types. While it only 
scratches the surface of what's possible, we encourage you to use that module 
instead of creating your own order specs.

.. _`example order specs`: https://developer.schwab.com/products/trader-api--individual/details/documentation/Retail%20Trader%20API%20Production

.. automethod:: schwab.client.Client.place_order

.. _accessing_existing_orders:

-------------------------
Accessing Existing Orders
-------------------------

When date bounds are omitted, account-specific queries request the preceding
365 days, while queries across all linked accounts request the preceding 60
days. These defaults reflect the different maximum ranges supported by the two
Schwab endpoints. Both methods default the end of the range to the current
time.

.. automethod:: schwab.client.Client.get_orders_for_account
.. automethod:: schwab.client.Client.get_orders_for_all_linked_accounts
.. automethod:: schwab.client.Client.get_order
.. autoclass:: schwab.client.Client.Order
  :members:
  :undoc-members:

-----------------------
Editing Existing Orders
-----------------------

Endpoints for canceling and replacing existing orders.

These endpoints require the order ID. Because the API does not return a JSON 
response when creating an order, the workflow for extracting this order ID is a 
little complicated.  You can fetch the order ID from the response to a 
:meth:`place_order <schwab.client.Client.place_order>` request using :ref:`this 
helper function <extract_order_id>`. Otherwise, see 
:ref:`accessing_existing_orders` to finding historical orders.

.. automethod:: schwab.client.Client.cancel_order
.. automethod:: schwab.client.Client.replace_order


+++++++++++++++
Other Endpoints
+++++++++++++++

Note If your account limited to delayed quotes, these quotes will also be 
delayed.

-------------------
Transaction History
-------------------

.. automethod:: schwab.client.Client.get_transaction
.. automethod:: schwab.client.Client.get_transactions
.. autoclass:: schwab.client.Client.Transactions
  :members:
  :undoc-members:

----------------
User Preferences
----------------

.. automethod:: schwab.client.Client.get_user_preferences

-------------
Market Movers
-------------

.. automethod:: schwab.client.Client.get_movers
.. autoclass:: schwab.client.Client.Movers
  :members:
  :undoc-members:


------------
Market Hours
------------

Several markets are sent as one comma-separated ``markets`` parameter, like
the other multi-valued parameters in this client.

.. automethod:: schwab.client.Client.get_market_hours
.. automethod:: schwab.client.Client.get_market_hours_for_market
.. autoclass:: schwab.client.Client.MarketHours
  :members:
  :undoc-members:



++++++++++++++++++++++++++++++++++++++
Date-only order and transaction bounds
++++++++++++++++++++++++++++++++++++++

For order queries on one or all linked accounts and for transaction queries,
``datetime.date`` bounds use UTC calendar days. A start date becomes midnight
at the beginning of that day; an end date becomes midnight at the beginning of
the following day. Passing the same date for both bounds therefore requests a
full UTC day. The endpoint determines whether an event exactly at the upper
boundary is included. Pass explicit timezone-aware ``datetime.datetime`` values
when exact boundaries or a market-local day are needed. Datetime values are
converted to UTC without advancing the end bound. Naive datetimes mean UTC and,
as for price history, emit a ``UserWarning``: a naive local time would shift
the query window by the local UTC offset.

++++++++++++++++++++++++++++++
Contextual price-history enums
++++++++++++++++++++++++++++++

``get_price_history`` infers omitted ``period_type`` and ``frequency_type`` from
``Period`` and ``Frequency`` enum members. Explicit types must match the member's
context, otherwise ``ValueError`` is raised before sending the request. For
example, ``Period.ONE_YEAR`` implies ``PeriodType.YEAR`` and cannot be paired
with ``PeriodType.DAY``. With enum enforcement disabled, raw numeric values
remain supported but cannot supply context for inference.


++++++++++++++++++++++++++++++++++++
Price-history datetime compatibility
++++++++++++++++++++++++++++++++++++

Price-history datetime bounds interpret naive datetimes as UTC and emit a
``UserWarning``. Earlier versions used the machine's local timezone through
``datetime.timestamp()``. Pass timezone-aware datetimes to preserve the intended
instant across machines and upgrades; for example, attach the intended local
zone with ``zoneinfo.ZoneInfo`` when constructing a market-local datetime.
Timezone-aware bounds are converted to epoch milliseconds without a warning.


+++++++++++++++++++++++++++++++++++++++++
Raw enum values and explicit query limits
+++++++++++++++++++++++++++++++++++++++++

Disabling enum enforcement allows raw API values, but members of an unrelated
enum are still rejected. Iterable enum parameters treat a bare string as one
value rather than a sequence of characters. Order queries forward any explicitly
provided ``max_results``, including zero; omitting it leaves the API default in
place. Forwarding a value does not guarantee that the API accepts it.

GET retries
+++++++++++

GET retries are disabled by default. To retry rate limits and selected server
errors, call ``client.configure_get_retries(max_retries=3)``. Backoff is bounded
by ``max_delay``; when a server's Retry-After exceeds that limit, the response is
returned without retrying. Other HTTP methods and transport errors are not
retried.

