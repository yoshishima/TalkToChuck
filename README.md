# `schwab-py`: A Charles Schwab API wrapper

[![Tests](https://github.com/yoshishima/schwab-py/actions/workflows/python.yml/badge.svg)](https://github.com/yoshishima/schwab-py/actions/workflows/python.yml)

This is a maintained fork of [alexgolec's original `schwab-py`](https://github.com/alexgolec/schwab-py),
created after the upstream project went a year without updates. Thank you to
Alex Golec (`alexgolec`) for creating `schwab-py` and building the foundation
this fork continues to develop.

## What is `schwab-py`?

`schwab-py` is an unofficial wrapper around the Charles Schwab Trader and
Market Data APIs. It provides a thin Python interface over the HTTP and
streaming endpoints while returning the original response data for callers to
interpret.

Notable functionality includes:

- OAuth login, token storage, and token refresh
- Account details, balances, positions, transactions, and order history
- Order placement, replacement, cancellation, templates, and code generation
- Quotes, fundamentals, instruments, movers, market hours, and price history
- Option chains and option expiration chains
- Streaming quotes, charts, account activity, and order book data

## Requirements

`schwab-py` requires Python 3.12 or newer. The test matrix covers Python 3.12,
3.13, and 3.14, with current development focused on Python 3.14 compatibility.

## Improvements in this fork

The current fork is version v1.7.4. Changes since the original project include:

- More reliable OAuth login and callback server startup, with clearer errors
  when the callback server times out or another process owns its port.
- Safer token file updates, including atomic replacement, restrictive file
  permissions, and correct handling of token paths that are symlinks.
- More robust streaming message delivery and handler dispatch, including
  improved reader lifecycle handling and malformed message handling.
- More precise order and option price calculations using `Decimal`, plus
  clearer validation of numeric inputs and order builder state.
- Corrected price history and order history query defaults, with validation
  for optional boolean query parameters.
- Updated dependencies and automated tests for Python 3.12, 3.13, and 3.14
  on Windows, macOS, and Linux.

## Installation

```console
python -m pip install git+https://github.com/yoshishima/schwab-py.git
```

This command installs the current fork from GitHub. The `schwab-py` package on
PyPI and the Read the Docs site are maintained separately from this fork and
may describe a different version.

Before using the library, create an account and application on the
[Charles Schwab developer site](https://developer.schwab.com/login). Record the
API key, app secret, and callback URL. The application must be approved by
Schwab before it can access the APIs, which can take several days.

See this repository's [getting-started guide](docs/getting-started.rst)
for detailed setup instructions.

## Quick start

The following example authenticates and requests available daily price history
for Apple:

```python
import json

from schwab import auth

api_key = 'YOUR_API_KEY'
app_secret = 'YOUR_APP_SECRET'
callback_url = 'https://127.0.0.1:8182/'
token_path = '/path/to/token.json'

client = auth.easy_client(
    api_key,
    app_secret,
    callback_url,
    token_path,
)

response = client.get_price_history_every_day('AAPL')
response.raise_for_status()
print(json.dumps(response.json(), indent=4))
```

Never commit or share API secrets or token files.

## Important API behavior

### Price history

The raw `Client.get_price_history` method supports period-based queries or an
explicit start and end time. The `get_price_history_every_*` convenience
methods use an explicit date range and do not send the redundant `period`
parameter. If omitted, their start time defaults to January 1, 1971 UTC and
their end time defaults to the current time. Schwab may limit the returned
history based on candle frequency.

### Order history

The order-history endpoints have different maximum ranges. When date bounds
are omitted:

- `get_orders_for_account` requests the preceding 365 days.
- `get_orders_for_all_linked_accounts` requests the preceding 60 days.

Both methods default the end of the range to the current time. Pass explicit
date bounds when a narrower range is required.

### Boolean query parameters

Optional boolean parameters must be actual Python `bool` values. For example,
pass `indicative=True`, not `indicative='true'`. This validation also applies to
`include_underlying_quote`, `need_extended_hours_data`, and
`need_previous_close`.

## Migrating from `tda-api`

The former TD Ameritrade APIs are no longer available, so `tda-api` cannot be
used for new requests. See the
[transition guide](docs/tda-transition.rst)
for migration instructions.

## Why use `schwab-py`?

1. **Safer authentication.** The library implements Schwab's OAuth callback
   flow and manages token refresh, avoiding the need to build security-sensitive
   authentication code from scratch.
2. **Minimal API wrapping.** Methods expose Schwab's endpoints without hiding
   the underlying HTTP responses, allowing callers to handle status codes and
   response payloads directly.
3. **Order-building utilities.** Templates and builders make common equity and
   option orders easier to construct while retaining access to raw order specs.
4. **Synchronous and asynchronous clients.** Applications can choose the model
   that best fits their workload, including asynchronous streaming support.

## Limitations

- The API is not connected to thinkorswim-specific functionality, although it
  can access and trade against the same eligible Schwab accounts.
- Paper trading is not supported.
- Historical option pricing data is not available.
- API availability, permissions, limits, and response formats are controlled by
  Charles Schwab and may change independently of this library.

## Documentation and support

The documentation for this fork is in the [docs directory](docs/). The
[upstream Read the Docs site](https://schwab-py.readthedocs.io/en/latest/)
may cover a different version. Community support is available through the
[upstream Discord server](https://discord.gg/BEr6y6Xqyv).

Bug reports and suggestions can be submitted through
[GitHub Issues](https://github.com/yoshishima/schwab-py/issues). Contributions are
welcome through [pull requests](https://github.com/yoshishima/schwab-py/pulls).

`schwab-py` is released under the
[MIT License](LICENSE).

## Disclaimer

`schwab-py` is an unofficial API wrapper. It is not endorsed by or affiliated
with Charles Schwab or any associated organization. Review and comply with the
terms of service for the underlying APIs. The project authors accept no
responsibility for damage resulting from use of this package. See the
[LICENSE](LICENSE) file for
details.
