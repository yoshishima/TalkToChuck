import math
import warnings
from contextlib import contextmanager
from typing import Any, Self

from decimal import (Decimal, Inexact, InvalidOperation, MAX_EMAX, MIN_EMIN,
                     ROUND_DOWN, ROUND_HALF_EVEN, Rounded, localcontext)

from schwab.orders import common
from schwab.utils import EnumEnforcer

import httpx2 as httpx

_MAX_PRICE_PRECISION = 10_000


def _is_finite_number(value):
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float, Decimal))
        and (not isinstance(value, float) or math.isfinite(value))
        and (not isinstance(value, Decimal) or value.is_finite())
    )


def _copy_price_value(value):
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError('price must be finite')
        return format(value, 'f')
    return value


def _build_object(obj, _path='order', _active=None):
    if isinstance(obj, Decimal):
        if not obj.is_finite():
            raise ValueError('numeric values must be finite (at {})'.format(_path))
        if obj == int(obj):
            return int(obj)
        value = float(obj)
        if not math.isfinite(value) or Decimal(str(value)) != obj:
            raise ValueError('Decimal cannot be represented as a JSON number without loss')
        return value
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError('numeric values must be finite (at {})'.format(_path))
        return obj
    # Literals are passed straight through
    if isinstance(obj, (str, int)):
        return obj

    # Note enums are not handled because call callers convert their enums to
    # values.
    if obj is None or isinstance(obj, (tuple, set, frozenset, bytes, bytearray)):
        raise ValueError('unsupported value of type {} in order (at {})'.format(
            type(obj).__name__, _path))

    # A builder that contains itself would otherwise recurse until it fails.
    if _active is None:
        _active = set()
    if id(obj) in _active:
        raise ValueError('order contains itself (at {})'.format(_path))
    _active.add(id(obj))
    try:
        # Dicts and lists are iterated over, with keys intact
        if isinstance(obj, dict):
            return {key: _build_object(value, '{}.{}'.format(_path, key), _active)
                    for key, value in obj.items()}
        if isinstance(obj, list):
            return [_build_object(value, '{}[{}]'.format(_path, index), _active)
                    for index, value in enumerate(obj)]

        # Objects have their variables translated into keys
        try:
            attributes = vars(obj)
        except TypeError:
            raise ValueError('unsupported value of type {} in order (at {})'.format(
                type(obj).__name__, _path)) from None
        ret = {}
        for name, value in attributes.items():
            if name == '_numeric_prices':
                continue
            if value is None or name[0] != '_':
                continue

            name = name[1:]
            ret[name] = _build_object(value, '{}.{}'.format(_path, name), _active)
        if isinstance(obj, OrderBuilder):
            obj._format_option_prices(ret)
        return ret
    finally:
        _active.discard(id(obj))


def _format_price_input(price):
    '''Validate and format a numeric price given to set_price or
    set_stop_price. Strings are handled by the setters themselves.'''
    if isinstance(price, bool) or not isinstance(price, (int, float, Decimal)):
        raise ValueError(
            'price must be a str, int, float or Decimal, got {}'.format(
                type(price).__name__))
    if not _is_finite_number(price):
        raise ValueError('price must be finite')
    if price < 0:
        # Credits are expressed with NET_CREDIT and a positive price.
        raise ValueError('price must not be negative')
    if isinstance(price, Decimal):
        return _format_numeric_price(price)
    return truncate_float(price, stacklevel=4)


def truncate_float(flt, *, stacklevel=2):
    if isinstance(flt, float):
        warnings.warn('passing floats to set_price and set_stop_price is '+
                      'deprecated and will be removed soon. Please update your '+
                      'code to pass prices as strings instead.',
                      DeprecationWarning, stacklevel=stacklevel)

    return _format_numeric_price(flt)


@contextmanager
def _price_decimal_context(value):
    # Price truncation is deliberate and must not depend on an application's
    # Decimal precision, rounding mode, or inexact/rounded traps.
    required_precision = max(28, len(value.as_tuple().digits) + 5,
                             value.adjusted() + 6)
    if required_precision > _MAX_PRICE_PRECISION:
        raise ValueError('price is too large to format')
    with localcontext() as context:
        context.prec = required_precision
        context.Emax = MAX_EMAX
        context.Emin = MIN_EMIN
        context.traps[Inexact] = False
        context.traps[Rounded] = False
        context.traps[InvalidOperation] = True
        yield


def _format_numeric_price(flt):
    value = Decimal(str(flt))
    if not value.is_finite():
        raise ValueError('price must be finite')

    with _price_decimal_context(value):
        decimal_places = 4 if abs(value) < 1 and value != 0 else 2
        quantum = Decimal(1).scaleb(-decimal_places)
        if isinstance(flt, float):
            nearest = value.quantize(quantum, rounding=ROUND_HALF_EVEN)
            # Recover tick boundaries obscured by float arithmetic. Never snap
            # to zero: nonzero prices below our precision must be rejected.
            if nearest != 0 and abs(value - nearest) <= Decimal('1e-9'):
                value = nearest
                decimal_places = 4 if abs(value) < 1 else 2
                quantum = Decimal(1).scaleb(-decimal_places)
        truncated = value.quantize(quantum, rounding=ROUND_DOWN)

        if value != 0 and truncated.is_zero():
            raise ValueError('nonzero price must not truncate to zero')

        # Normalize explicitly supplied negative zero.
        if truncated.is_zero():
            truncated = abs(truncated)
        return format(truncated, f'.{decimal_places}f')


class OrderBuilder(EnumEnforcer):
    '''
    Helper class to create arbitrarily complex orders. Note this class simply
    implements the order schema defined by the `Schwab Trader API
    <https://developer.schwab.com/>`__, with no attempts to validate the result.
    Orders created using this class may be rejected or may never fill. Use at
    your own risk.
    '''

    def __init__(self, *, enforce_enums=True):
        super().__init__(enforce_enums)
        self._numeric_prices = set()

        self._session = None
        self._duration = None
        self._orderType = None
        self._cancelTime = None
        self._complexOrderStrategyType = None
        self._quantity = None
        self._destinationLinkName = None
        self._releaseTime = None
        self._stopPrice = None
        self._stopPriceLinkBasis = None
        self._stopPriceLinkType = None
        self._stopPriceOffset = None
        self._stopType = None
        self._priceLinkBasis = None
        self._priceLinkType = None
        self._price = None
        self._taxLotMethod = None
        self._orderLegCollection = None
        self._activationPrice = None
        self._specialInstruction = None
        self._orderStrategyType = None
        self._childOrderStrategies = None

    # Session
    def set_session(self, session):
        '''
        Set the order session. See :class:`~schwab.orders.common.Session` for
        details.
        '''
        session = self.convert_enum(session, common.Session)
        self._session = session
        return self

    def clear_session(self):
        '''
        Clear the order session.
        '''
        self._session = None
        return self

    # Duration
    def set_duration(self, duration):
        '''
        Set the order duration. See :class:`~schwab.orders.common.Duration` for
        details.
        '''
        duration = self.convert_enum(duration, common.Duration)
        self._duration = duration
        return self

    def clear_duration(self):
        '''
        Clear the order duration.
        '''
        self._duration = None
        return self

    # OrderType
    def set_order_type(self, order_type):
        '''
        Set the order type. See :class:`~schwab.orders.common.OrderType` for
        details.
        '''
        order_type = self.convert_enum(order_type, common.OrderType)
        self._orderType = order_type
        return self

    def clear_order_type(self):
        '''
        Clear the order type.
        '''
        self._orderType = None
        return self

    # CancelTime
    def set_cancel_time(self, cancel_time):
        '''Set the order cancellation time as an ISO-8601 string.'''
        if not isinstance(cancel_time, str):
            raise ValueError('cancel_time must be passed as str')
        self._cancelTime = cancel_time
        return self

    def clear_cancel_time(self):
        '''Clear the order cancellation time.'''
        self._cancelTime = None
        return self

    # ComplexOrderStrategyType
    def set_complex_order_strategy_type(self, complex_order_strategy_type):
        '''
        Set the complex order strategy type. See
        :class:`~schwab.orders.common.ComplexOrderStrategyType` for details.
        '''
        complex_order_strategy_type = self.convert_enum(
            complex_order_strategy_type, common.ComplexOrderStrategyType)
        self._complexOrderStrategyType = complex_order_strategy_type
        return self

    def clear_complex_order_strategy_type(self):
        '''
        Clear the complex order strategy type.
        '''
        self._complexOrderStrategyType = None
        return self

    # Quantity
    def set_quantity(self, quantity):
        '''
        Exact semantics unknown. See :ref:`undocumented_quantity` for a
        discussion.
        '''
        if not _is_finite_number(quantity) or quantity <= 0:
            raise ValueError('quantity must be positive and finite')
        self._quantity = quantity
        return self

    def clear_quantity(self):
        '''
        Clear the order-level quantity. Note this does not affect order legs.
        '''
        self._quantity = None
        return self

    # DestinationLinkName
    def set_destination_link_name(self, destination_link_name):
        '''
        Set the destination link name from Schwab's free-form request field.

        :class:`~schwab.orders.common.Destination` is also accepted for
        backward compatibility, though that enum represents the separate
        response-only ``requestedDestination`` field.
        '''
        if isinstance(destination_link_name, common.Destination):
            destination_link_name = destination_link_name.value
        elif not isinstance(destination_link_name, str):
            raise ValueError('destination_link_name must be passed as str')
        self._destinationLinkName = destination_link_name
        return self

    def clear_destination_link_name(self):
        '''
        Clear the destination link name
        '''
        self._destinationLinkName = None
        return self

    # ReleaseTime
    def set_release_time(self, release_time):
        '''Set the order release time as an ISO-8601 string.'''
        if not isinstance(release_time, str):
            raise ValueError('release_time must be passed as str')
        self._releaseTime = release_time
        return self

    def clear_release_time(self):
        '''Clear the order release time.'''
        self._releaseTime = None
        return self

    # StopPrice
    def set_stop_price(self, stop_price):
        '''
        Set the stop price. Note price can be passed as either a `float` or an
        `str`. See :ref:`number_truncation`.
        '''
        if isinstance(stop_price, str):
            self._stopPrice = stop_price
            self._numeric_prices.discard('stopPrice')
        else:
            self._stopPrice = _format_price_input(stop_price)
            self._numeric_prices.add('stopPrice')
        return self

    def copy_stop_price(self, stop_price):
        '''
        Directly set the stop price, avoiding all the validation and truncation
        logic from :func:`set_stop_price`.
        '''
        self._stopPrice = _copy_price_value(stop_price)
        self._numeric_prices.discard('stopPrice')
        return self

    def clear_stop_price(self):
        '''
        Clear the stop price.
        '''
        self._stopPrice = None
        self._numeric_prices.discard('stopPrice')
        return self

    # StopPriceLinkBasis
    def set_stop_price_link_basis(self, stop_price_link_basis):
        '''
        Set the stop price link basis. See
        :class:`~schwab.orders.common.StopPriceLinkBasis` for details.
        '''
        stop_price_link_basis = self.convert_enum(
            stop_price_link_basis, common.StopPriceLinkBasis)
        self._stopPriceLinkBasis = stop_price_link_basis
        return self

    def clear_stop_price_link_basis(self):
        '''
        Clear the stop price link basis.
        '''
        self._stopPriceLinkBasis = None
        return self

    # StopPriceLinkType
    def set_stop_price_link_type(self, stop_price_link_type):
        '''
        Set the stop price link type. See
        :class:`~schwab.orders.common.StopPriceLinkType` for details.
        '''
        stop_price_link_type = self.convert_enum(
            stop_price_link_type, common.StopPriceLinkType)
        self._stopPriceLinkType = stop_price_link_type
        return self

    def clear_stop_price_link_type(self):
        '''
        Clear the stop price link type.
        '''
        self._stopPriceLinkType = None
        return self

    # StopPriceOffset
    def set_stop_price_offset(self, stop_price_offset):
        '''
        Set the stop price offset.
        '''
        if not _is_finite_number(stop_price_offset):
            raise ValueError('stop price offset must be finite')
        self._stopPriceOffset = stop_price_offset
        return self

    def clear_stop_price_offset(self):
        '''
        Clear the stop price offset.
        '''
        self._stopPriceOffset = None
        return self

    # StopType
    def set_stop_type(self, stop_type):
        '''
        Set the stop type. See
        :class:`~schwab.orders.common.StopType` for more details.
        '''
        stop_type = self.convert_enum(stop_type, common.StopType)
        self._stopType = stop_type
        return self

    def clear_stop_type(self):
        '''
        Clear the stop type.
        '''
        self._stopType = None
        return self

    # PriceLinkBasis
    def set_price_link_basis(self, price_link_basis):
        '''
        Set the price link basis. See
        :class:`~schwab.orders.common.PriceLinkBasis` for details.
        '''
        price_link_basis = self.convert_enum(
            price_link_basis, common.PriceLinkBasis)
        self._priceLinkBasis = price_link_basis
        return self

    def clear_price_link_basis(self):
        '''
        Clear the price link basis.
        '''
        self._priceLinkBasis = None
        return self

    # PriceLinkType
    def set_price_link_type(self, price_link_type):
        '''
        Set the price link type. See
        :class:`~schwab.orders.common.PriceLinkType` for more details.
        '''
        price_link_type = self.convert_enum(
            price_link_type, common.PriceLinkType)
        self._priceLinkType = price_link_type
        return self

    def clear_price_link_type(self):
        '''
        Clear the price link basis.
        '''
        self._priceLinkType = None
        return self

    # Price
    def set_price(self, price):
        '''
        Set the order price. Note price can be passed as either a `float` or an
        `str`. See :ref:`number_truncation`.
        '''
        if isinstance(price, str):
            self._price = price
            self._numeric_prices.discard('price')
        else:
            self._price = _format_price_input(price)
            self._numeric_prices.add('price')
        return self

    def copy_price(self, price: Any) -> Self:
        '''
        Directly set the price, avoiding all the validation and truncation
        logic from :func:`set_price`.
        '''
        self._price = _copy_price_value(price)
        self._numeric_prices.discard('price')
        return self

    def clear_price(self):
        '''
        Clear the order price
        '''
        self._price = None
        self._numeric_prices.discard('price')
        return self

    # TaxLotMethod
    def set_tax_lot_method(self, tax_lot_method):
        '''Set the tax-lot selection method.'''
        tax_lot_method = self.convert_enum(
                tax_lot_method, common.TaxLotMethod)
        self._taxLotMethod = tax_lot_method
        return self

    def clear_tax_lot_method(self):
        '''Clear the tax-lot selection method.'''
        self._taxLotMethod = None
        return self

    # ActivationPrice
    def set_activation_price(self, activation_price):
        '''
        Set the activation price.
        '''
        if (not _is_finite_number(activation_price)
                or activation_price <= 0.0):
            raise ValueError('activation price must be positive and finite')
        self._activationPrice = activation_price
        return self

    def clear_activation_price(self):
        '''
        Clear the activation price.
        '''
        self._activationPrice = None
        return self

    # SpecialInstruction
    def set_special_instruction(self, special_instruction):
        '''
        Set the special instruction. See
        :class:`~schwab.orders.common.SpecialInstruction` for details.
        '''
        special_instruction = self.convert_enum(
            special_instruction, common.SpecialInstruction)
        self._specialInstruction = special_instruction
        return self

    def clear_special_instruction(self):
        '''
        Clear the special instruction.
        '''
        self._specialInstruction = None
        return self

    # OrderStrategyType
    def set_order_strategy_type(self, order_strategy_type):
        '''
        Set the order strategy type. See
        :class:`~schwab.orders.common.OrderStrategyType` for more details.
        '''
        order_strategy_type = self.convert_enum(
            order_strategy_type, common.OrderStrategyType)
        self._orderStrategyType = order_strategy_type
        return self

    def clear_order_strategy_type(self):
        '''
        Clear the order strategy type.
        '''
        self._orderStrategyType = None
        return self

    # ChildOrderStrategies
    def add_child_order_strategy(self, child_order_strategy):
        if isinstance(child_order_strategy, httpx.Response):
            raise ValueError(
                    'Child order cannot be a response. See here for ' +
                    'details: https://schwab-api.readthedocs.io/en/latest/' +
                    'order-templates.html#utility-methods')

        if (not isinstance(child_order_strategy, OrderBuilder)
                and not isinstance(child_order_strategy, dict)):
            raise ValueError('child order must be OrderBuilder or dict')

        if self._childOrderStrategies is None:
            self._childOrderStrategies = []

        self._childOrderStrategies.append(child_order_strategy)
        return self

    def clear_child_order_strategies(self):
        self._childOrderStrategies = None
        return self

    # OrderLegCollection
    def __add_order_leg(self, instruction, instrument, quantity):
        # instruction is assumed to have been verified

        if not isinstance(instrument._symbol, str) or not instrument._symbol.strip():
            raise ValueError('symbol must be a non-empty string')
        if not _is_finite_number(quantity) or quantity <= 0:
            raise ValueError('quantity must be positive and finite')

        if self._orderLegCollection is None:
            self._orderLegCollection = []

        self._orderLegCollection.append({
            'instruction': instruction,
            'instrument': instrument,
            'quantity': quantity,
        })

        return self

    def add_equity_leg(self, instruction, symbol, quantity):
        '''
        Add an equity order leg.

        :param instruction: Instruction for the leg. See
                            :class:`~schwab.orders.common.EquityInstruction` for
                            valid options.
        :param symbol: Equity symbol
        :param quantity: Number of shares for the order
        '''
        instruction = self.convert_enum(instruction, common.EquityInstruction)
        return self.__add_order_leg(
            instruction, common.EquityInstrument(symbol), quantity)

    def add_option_leg(self, instruction, symbol, quantity):
        '''
        Add an option order leg.

        :param instruction: Instruction for the leg. See
                            :class:`~schwab.orders.common.OptionInstruction` for
                            valid options.
        :param symbol: Option symbol
        :param quantity: Number of contracts for the order
        '''
        if not _is_finite_number(quantity) or quantity <= 0:
            raise ValueError('quantity must be positive and finite')
        if quantity != int(quantity):
            raise ValueError('option quantity must be a positive whole number')
        instruction = self.convert_enum(instruction, common.OptionInstruction)
        return self.__add_order_leg(
            instruction, common.OptionInstrument(symbol), quantity)

    def clear_order_legs(self):
        '''
        Clear all order legs.
        '''
        self._orderLegCollection = None
        return self

    # Build

    def _format_option_prices(self, order):
        legs = order.get('orderLegCollection', [])
        if not legs or not all(
                leg['instrument']['assetType'] == 'OPTION' for leg in legs):
            return
        for field in self._numeric_prices:
            value = Decimal(order[field])
            with _price_decimal_context(value):
                truncated = value.quantize(Decimal('0.01'), rounding=ROUND_DOWN)
            if value != 0 and truncated.is_zero():
                raise ValueError('nonzero option price must not truncate to zero')
            order[field] = format(truncated, '.2f')

    def build(self) -> dict[str, Any]:
        return _build_object(self)
