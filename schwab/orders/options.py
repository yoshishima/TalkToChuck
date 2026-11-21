import datetime
from decimal import Decimal, DecimalException

from schwab.orders.generic import OrderBuilder


def _parse_expiration_date(expiration_date):
    if len(expiration_date) == 6 and expiration_date.isascii() and expiration_date.isdigit():
        try:
            return datetime.datetime.strptime(expiration_date, '%y%m%d').date()
        except ValueError:
            pass

    raise ValueError(
        'expiration date must follow format ' +
        '[two digit year][Month with leading zero]' +
        '[Day with leading zero]')


def _format_strike_price(strike_millis):
    whole, fractional = divmod(strike_millis, 1000)
    if fractional:
        return '{}.{:03d}'.format(whole, fractional).rstrip('0')
    return str(whole)


def _strike_price_to_millis(value):
    error = ('strike price must be a string representing a positive '
             'number with at most three decimal places')
    if not isinstance(value, str):
        raise ValueError(error)
    try:
        strike = Decimal(value)
        if not strike.is_finite() or strike <= 0:
            raise ValueError(error)
    except DecimalException:
        raise ValueError(error) from None

    # Work on the exact coefficient and exponent. Decimal arithmetic would
    # round to the caller's context precision before we could validate it.
    _, digits, exponent = strike.as_tuple()
    digits = list(digits)
    while digits[-1] == 0:
        digits.pop()
        exponent += 1
    exponent += 3
    if exponent < 0:
        raise ValueError(error)
    if len(digits) + exponent > 8:
        raise ValueError(
            'strike price cannot be encoded in the eight-digit OSI field')
    return int(''.join(map(str, digits))) * 10 ** exponent


class OptionSymbol:
    '''Construct an option symbol from its constituent parts.

    :param underlying_symbol: Symbol of the underlying. Not validated.
    :param expiration_date: Expiration date. Accepts ``datetime.date``,
                            ``datetime.datetime``, or strings with the
                            format ``[Two digit year][Two digit month][Two
                            digit day]``.
    :param contract_type: ``P`` or ``PUT`` for put and ``C`` or ``CALL`` for 
                          call.
    :param strike_price_as_string: Strike price represented as a decimal string.

    Note while each of the individual parts is validated by itself, the
    option symbol itself may not represent a traded option:

     * Some underlyings do not support options.
     * Not all dates have valid option expiration dates.
     * Not all strike prices are valid options strikes.

    You can use :meth:`~schwab.client.Client.get_option_chain` to obtain real
    option symbols for an underlying, as well as extensive data in pricing,
    bid/ask spread, volume, etc.

    For those interested in the details, options symbols have the following 
    format: ``[Underlying left justified with spaces to 6 positions] [Two digit 
    year][Two digit month][Two digit day]['P' or 'C'][Strike price]``

    The format of the strike price is modified based on its amount:
     * If less than 1000, Strike Price is multiple by 1000 and pre-pended with
       two zeroes
     * If greater than 1000, it's prepended with one zero.

    Examples include:
     * ``QQQ   240420P00500000``: QQQ Apr 20, 2024 500 Put (note the two zeroes
       in front because strike is less than 1000)
     * ``SPXW  240420C05040000``: SPX Weekly Apr 20, 2024 5040 Call (note the
       one zero in front because strike is greater than 1000)

    '''

    def __init__(self, underlying_symbol, expiration_date, contract_type,
                 strike_price_as_string):
        self.underlying_symbol = underlying_symbol

        if contract_type in ('C', 'CALL'):
            self.contract_type = 'C'
        elif contract_type in ('P', 'PUT'):
            self.contract_type = 'P'
        else:
            raise ValueError(
                'Contract type must be one of \'C\', \'CALL\', \'P\' or \'PUT\'')

        if isinstance(expiration_date, str):
            self.expiration_date = _parse_expiration_date(expiration_date)
        elif isinstance(expiration_date, datetime.datetime):
            self.expiration_date = datetime.date(
                year=expiration_date.year,
                month=expiration_date.month,
                day=expiration_date.day)
        elif isinstance(expiration_date, datetime.date):
            self.expiration_date = expiration_date
        else:
            raise ValueError(
                'expiration_date must be a string with format %y%m%d ' +
                '(e.g. 240614) or one of datetime.date or ' +
                'datetime.datetime')

        strike_millis = _strike_price_to_millis(strike_price_as_string)
        self.strike_price = _format_strike_price(strike_millis)

    @classmethod
    def parse_symbol(cls, symbol):
        '''
        Parse a string option symbol of the for ``[Underlying left justified to 6 positions][Two digit year]
        [Two digit month][Two digit day]['P' or 'C'][Strike price]``.
        '''
        format_error_str = (
            'option symbol must have format ' +
            '[Underlying left justified with spaces to 6 positions][Expiration][P/C][Strike]')

        # Underlying
        underlying = symbol[:6].rstrip()
        rest = symbol[6:]

        # Expiration
        type_split = rest.split('P')
        if len(type_split) == 2:
            expiration_date, strike = type_split
            contract_type = 'P'
        else:
            type_split = rest.split('C')
            if len(type_split) == 2:
                expiration_date, strike = type_split
                contract_type = 'C'
            else:
                raise ValueError(
                    r'option must have contract type \'C\' r \'\P\', ' +
                    format_error_str)

        if len(strike) != 8 or not strike.isdigit():
            raise ValueError(format_error_str)
        strike = _format_strike_price(int(strike))

        expiration_date = _parse_expiration_date(expiration_date)

        return OptionSymbol(underlying, expiration_date, contract_type, strike)

    def build(self):
        '''
        Returns the option symbol represented by this builder.
        '''
        return '{:<6}{}{}{:08d}'.format(
            self.underlying_symbol,
            self.expiration_date.strftime('%y%m%d'),
            self.contract_type,
            _strike_price_to_millis(self.strike_price)
        )


def __base_builder():
    from schwab.orders.common import Duration, Session

    return (OrderBuilder()
            .set_session(Session.NORMAL)
            .set_duration(Duration.DAY))


################################################################################
# Single options

# Buy to Open

def option_buy_to_open_market(symbol, quantity):
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` for a
    buy-to-open market order.
    '''
    from schwab.orders.common import OptionInstruction, OrderType, OrderStrategyType

    return (__base_builder()
            .set_order_type(OrderType.MARKET)
            .set_order_strategy_type(OrderStrategyType.SINGLE)
            .add_option_leg(OptionInstruction.BUY_TO_OPEN, symbol, quantity))


def option_buy_to_open_limit(symbol, quantity, price):
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` for a
    buy-to-open limit order.
    '''
    from schwab.orders.common import OptionInstruction, OrderType, OrderStrategyType

    return (__base_builder()
            .set_order_type(OrderType.LIMIT)
            .set_price(price)
            .set_order_strategy_type(OrderStrategyType.SINGLE)
            .add_option_leg(OptionInstruction.BUY_TO_OPEN, symbol, quantity)
            )


# Sell to Open

def option_sell_to_open_market(symbol, quantity):
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` for a
    sell-to-open market order.
    '''
    from schwab.orders.common import OptionInstruction, OrderType, OrderStrategyType

    return (__base_builder()
            .set_order_type(OrderType.MARKET)
            .set_order_strategy_type(OrderStrategyType.SINGLE)
            .add_option_leg(OptionInstruction.SELL_TO_OPEN, symbol, quantity))


def option_sell_to_open_limit(symbol, quantity, price):
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` for a
    sell-to-open limit order.
    '''
    from schwab.orders.common import OptionInstruction, OrderType, OrderStrategyType

    return (__base_builder()
            .set_order_type(OrderType.LIMIT)
            .set_price(price)
            .set_order_strategy_type(OrderStrategyType.SINGLE)
            .add_option_leg(OptionInstruction.SELL_TO_OPEN, symbol, quantity)
            )


# Buy to Close


def option_buy_to_close_market(symbol, quantity):
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` for a
    buy-to-close market order.
    '''
    from schwab.orders.common import OptionInstruction, OrderType, OrderStrategyType

    return (__base_builder()
            .set_order_type(OrderType.MARKET)
            .set_order_strategy_type(OrderStrategyType.SINGLE)
            .add_option_leg(OptionInstruction.BUY_TO_CLOSE, symbol, quantity))


def option_buy_to_close_limit(symbol, quantity, price):
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` for a
    buy-to-close limit order.
    '''
    from schwab.orders.common import OptionInstruction, OrderType, OrderStrategyType

    return (__base_builder()
            .set_order_type(OrderType.LIMIT)
            .set_price(price)
            .set_order_strategy_type(OrderStrategyType.SINGLE)
            .add_option_leg(OptionInstruction.BUY_TO_CLOSE, symbol, quantity)
            )


# Sell to Close


def option_sell_to_close_market(symbol, quantity):
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` for a
    sell-to-close market order.
    '''
    from schwab.orders.common import OptionInstruction, OrderType, OrderStrategyType

    return (__base_builder()
            .set_order_type(OrderType.MARKET)
            .set_order_strategy_type(OrderStrategyType.SINGLE)
            .add_option_leg(OptionInstruction.SELL_TO_CLOSE, symbol, quantity))


def option_sell_to_close_limit(symbol, quantity, price):
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` for a
    sell-to-close limit order.
    '''
    from schwab.orders.common import OptionInstruction, OrderType, OrderStrategyType

    return (__base_builder()
            .set_order_type(OrderType.LIMIT)
            .set_price(price)
            .set_order_strategy_type(OrderStrategyType.SINGLE)
            .add_option_leg(OptionInstruction.SELL_TO_CLOSE, symbol, quantity)
            )


################################################################################
# Verticals

def _validate_vertical(first_symbol, second_symbol, contract_type):
    # All templates take the lower-strike leg first. Preserve support for
    # opaque symbols, which cannot be checked locally.
    if not isinstance(first_symbol, str) or not isinstance(second_symbol, str):
        return
    try:
        first = OptionSymbol.parse_symbol(first_symbol)
        second = OptionSymbol.parse_symbol(second_symbol)
    except ValueError:
        return
    if first.contract_type != contract_type or second.contract_type != contract_type:
        raise ValueError('vertical legs have the wrong contract type')
    if first.underlying_symbol != second.underlying_symbol:
        raise ValueError('vertical legs must have matching underlyings')
    if first.expiration_date != second.expiration_date:
        raise ValueError('vertical legs must have matching expirations')
    if Decimal(first.strike_price) >= Decimal(second.strike_price):
        raise ValueError('first vertical leg must have a lower strike than second')


# Bull Call

def bull_call_vertical_open(
        long_call_symbol, short_call_symbol, quantity, net_debit):
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` that opens a
    bull call vertical position. See :ref:`vertical_spreads` for details.
    '''
    from schwab.orders.common import OptionInstruction, OrderType, OrderStrategyType
    from schwab.orders.common import ComplexOrderStrategyType

    _validate_vertical(long_call_symbol, short_call_symbol, 'C')

    return (__base_builder()
            .set_order_type(OrderType.NET_DEBIT)
            .set_complex_order_strategy_type(ComplexOrderStrategyType.VERTICAL)
            .set_price(net_debit)
            .set_quantity(quantity)
            .set_order_strategy_type(OrderStrategyType.SINGLE)
            .add_option_leg(
                OptionInstruction.BUY_TO_OPEN, long_call_symbol, quantity)
            .add_option_leg(
                OptionInstruction.SELL_TO_OPEN, short_call_symbol, quantity))


def bull_call_vertical_close(
        long_call_symbol, short_call_symbol, quantity, net_credit):
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` that closes a
    bull call vertical position. See :ref:`vertical_spreads` for details.
    '''
    from schwab.orders.common import OptionInstruction, OrderType, OrderStrategyType
    from schwab.orders.common import ComplexOrderStrategyType

    _validate_vertical(long_call_symbol, short_call_symbol, 'C')

    return (__base_builder()
            .set_order_type(OrderType.NET_CREDIT)
            .set_complex_order_strategy_type(ComplexOrderStrategyType.VERTICAL)
            .set_price(net_credit)
            .set_quantity(quantity)
            .set_order_strategy_type(OrderStrategyType.SINGLE)
            .add_option_leg(
                OptionInstruction.SELL_TO_CLOSE, long_call_symbol, quantity)
            .add_option_leg(
                OptionInstruction.BUY_TO_CLOSE, short_call_symbol, quantity))


# Bear Call

def bear_call_vertical_open(
        short_call_symbol, long_call_symbol, quantity, net_credit):
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` that opens a
    bear call vertical position. See :ref:`vertical_spreads` for details.
    '''
    from schwab.orders.common import OptionInstruction, OrderType, OrderStrategyType
    from schwab.orders.common import ComplexOrderStrategyType

    _validate_vertical(short_call_symbol, long_call_symbol, 'C')

    return (__base_builder()
            .set_order_type(OrderType.NET_CREDIT)
            .set_complex_order_strategy_type(ComplexOrderStrategyType.VERTICAL)
            .set_price(net_credit)
            .set_quantity(quantity)
            .set_order_strategy_type(OrderStrategyType.SINGLE)
            .add_option_leg(
                OptionInstruction.SELL_TO_OPEN, short_call_symbol, quantity)
            .add_option_leg(
                OptionInstruction.BUY_TO_OPEN, long_call_symbol, quantity))


def bear_call_vertical_close(
        short_call_symbol, long_call_symbol, quantity, net_debit):
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` that closes a
    bear call vertical position. See :ref:`vertical_spreads` for details.
    '''
    from schwab.orders.common import OptionInstruction, OrderType, OrderStrategyType
    from schwab.orders.common import ComplexOrderStrategyType

    _validate_vertical(short_call_symbol, long_call_symbol, 'C')

    return (__base_builder()
            .set_order_type(OrderType.NET_DEBIT)
            .set_complex_order_strategy_type(ComplexOrderStrategyType.VERTICAL)
            .set_price(net_debit)
            .set_quantity(quantity)
            .set_order_strategy_type(OrderStrategyType.SINGLE)
            .add_option_leg(
                OptionInstruction.BUY_TO_CLOSE, short_call_symbol, quantity)
            .add_option_leg(
                OptionInstruction.SELL_TO_CLOSE, long_call_symbol, quantity))


# Bull Put

def bull_put_vertical_open(
        long_put_symbol, short_put_symbol, quantity, net_credit):
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` that opens a
    bull put vertical position. See :ref:`vertical_spreads` for details.
    '''
    from schwab.orders.common import OptionInstruction, OrderType, OrderStrategyType
    from schwab.orders.common import ComplexOrderStrategyType

    _validate_vertical(long_put_symbol, short_put_symbol, 'P')

    return (__base_builder()
            .set_order_type(OrderType.NET_CREDIT)
            .set_complex_order_strategy_type(ComplexOrderStrategyType.VERTICAL)
            .set_price(net_credit)
            .set_quantity(quantity)
            .set_order_strategy_type(OrderStrategyType.SINGLE)
            .add_option_leg(
                OptionInstruction.BUY_TO_OPEN, long_put_symbol, quantity)
            .add_option_leg(
                OptionInstruction.SELL_TO_OPEN, short_put_symbol, quantity))


def bull_put_vertical_close(
        long_put_symbol, short_put_symbol, quantity, net_debit):
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` that closes a
    bull put vertical position. See :ref:`vertical_spreads` for details.
    '''
    from schwab.orders.common import OptionInstruction, OrderType, OrderStrategyType
    from schwab.orders.common import ComplexOrderStrategyType

    _validate_vertical(long_put_symbol, short_put_symbol, 'P')

    return (__base_builder()
            .set_order_type(OrderType.NET_DEBIT)
            .set_complex_order_strategy_type(ComplexOrderStrategyType.VERTICAL)
            .set_price(net_debit)
            .set_quantity(quantity)
            .set_order_strategy_type(OrderStrategyType.SINGLE)
            .add_option_leg(
                OptionInstruction.SELL_TO_CLOSE, long_put_symbol, quantity)
            .add_option_leg(
                OptionInstruction.BUY_TO_CLOSE, short_put_symbol, quantity))


# Bear Put

def bear_put_vertical_open(
        short_put_symbol, long_put_symbol, quantity, net_debit):
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` that opens a
    bear put vertical position. See :ref:`vertical_spreads` for details.
    '''
    from schwab.orders.common import OptionInstruction, OrderType, OrderStrategyType
    from schwab.orders.common import ComplexOrderStrategyType

    _validate_vertical(short_put_symbol, long_put_symbol, 'P')

    return (__base_builder()
            .set_order_type(OrderType.NET_DEBIT)
            .set_complex_order_strategy_type(ComplexOrderStrategyType.VERTICAL)
            .set_price(net_debit)
            .set_quantity(quantity)
            .set_order_strategy_type(OrderStrategyType.SINGLE)
            .add_option_leg(
                OptionInstruction.SELL_TO_OPEN, short_put_symbol, quantity)
            .add_option_leg(
                OptionInstruction.BUY_TO_OPEN, long_put_symbol, quantity))


def bear_put_vertical_close(
        short_put_symbol, long_put_symbol, quantity, net_credit):
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` that closes a
    bear put vertical position. See :ref:`vertical_spreads` for details.
    '''
    from schwab.orders.common import OptionInstruction, OrderType, OrderStrategyType
    from schwab.orders.common import ComplexOrderStrategyType

    _validate_vertical(short_put_symbol, long_put_symbol, 'P')

    return (__base_builder()
            .set_order_type(OrderType.NET_CREDIT)
            .set_complex_order_strategy_type(ComplexOrderStrategyType.VERTICAL)
            .set_price(net_credit)
            .set_quantity(quantity)
            .set_order_strategy_type(OrderStrategyType.SINGLE)
            .add_option_leg(
                OptionInstruction.BUY_TO_CLOSE, short_put_symbol, quantity)
            .add_option_leg(
                OptionInstruction.SELL_TO_CLOSE, long_put_symbol, quantity))
