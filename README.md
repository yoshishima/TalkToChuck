# schwab-py

An unofficial Python wrapper for the Charles Schwab Trader and Market Data
APIs. Use it to authenticate, retrieve account and market data, build and manage
orders, and subscribe to streaming data.

[![Tests](https://github.com/yoshishima/TalkToChuck/actions/workflows/python.yml/badge.svg)](https://github.com/yoshishima/TalkToChuck/actions/workflows/python.yml)

This project builds on [the original schwab-py](https://github.com/alexgolec/schwab-py).
Thank you to **Alex Golec** for creating the library and sharing the work that
made this fork possible.

## Features

- OAuth authentication, token storage, and refresh
- Account balances, positions, transactions, and order history
- Order placement, replacement, cancellation, and order-building utilities
- Quotes, price history, fundamentals, instruments, movers, and market hours
- Option chains and expiration dates
- Streaming quotes, charts, account activity, and order book data
- Synchronous and asynchronous clients

The client returns HTTP responses so you can inspect status codes and work
directly with Schwab's response data.

## Requirements

- Python 3.12 or newer. CI tests Python 3.12, 3.13, and 3.14 on Windows,
  macOS, and Linux.
- A [Schwab developer account](https://developer.schwab.com/) and an approved
  application with an app key, app secret, and callback URL.

Application approval can take several days. See the
[getting-started guide](docs/getting-started.rst) for details. Its installation
instructions describe the upstream PyPI package; use the command below for this
fork.

## Installation

```console
python -m pip install git+https://github.com/yoshishima/TalkToChuck.git
```

This installs the current version from this repository. The package published
on PyPI and the upstream documentation may describe a different version.

## Quick start

Register a callback URL with your Schwab application and use the exact same
value below, including any trailing slash. Set `SCHWAB_API_KEY` and
`SCHWAB_APP_SECRET` in your environment before running the example.

```python
import json
import os

from schwab import auth

client = auth.easy_client(
    api_key=os.environ['SCHWAB_API_KEY'],
    app_secret=os.environ['SCHWAB_APP_SECRET'],
    callback_url='https://127.0.0.1:8182',
    token_path='token.json',
)

response = client.get_price_history_every_day('AAPL')
response.raise_for_status()
print(json.dumps(response.json(), indent=2))
```

On first use, follow the login prompts to create a token. Later runs can reuse
the token file, and the client updates it when tokens are refreshed. Keep your
app secret and token file private; do not commit them to version control. See
the [authentication guide](docs/auth.rst) for other login options.

## API notes

- **Price history:** `get_price_history_every_*` methods use a date range. If
  omitted, the start defaults to January 1, 1971 UTC and the end defaults to
  the current time. Schwab may limit the history returned for a given candle
  frequency.
- **Order history:** With no date bounds, `get_orders_for_account` requests
  the preceding 365 days, while `get_orders_for_all_linked_accounts` requests
  the preceding 60 days. Both default the end time to the current time.
- **Boolean parameters:** Pass Python booleans such as `True` and `False` for
  optional boolean query parameters, including `indicative`,
  `include_underlying_quote`, `need_extended_hours_data`, and
  `need_previous_close`.

## Documentation and contributing

- [Getting started](docs/getting-started.rst) and [authentication](docs/auth.rst)
- [Client API](docs/client.rst), [streaming](docs/streaming.rst), and
  [order builders](docs/order-builder.rst)
- [Order templates](docs/order-templates.rst) and
  [migration from tda-api](docs/tda-transition.rst)
- [Contributing guide](CONTRIBUTING.rst) and [changelog](CHANGELOG.md)

The [upstream documentation](https://schwab-py.readthedocs.io/en/latest/) may
cover a different version. Report bugs or suggest improvements using
[GitHub Issues](https://github.com/yoshishima/TalkToChuck/issues), or contribute
through a [pull request](https://github.com/yoshishima/TalkToChuck/pulls).

## Limitations

Paper trading and historical option pricing data are not supported. The API
does not provide thinkorswim-specific functionality, though it can access and
trade against eligible Schwab accounts. Schwab controls API availability,
permissions, limits, and response formats.

## License and disclaimer

Released under the [MIT License](LICENSE). This project is unofficial and is
not affiliated with or endorsed by Charles Schwab. Review the terms of service
for the APIs you use; see the license for warranty and liability details.
