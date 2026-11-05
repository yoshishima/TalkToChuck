import autopep8
import schwab

from collections import defaultdict


class UnrepeatableOrderError(ValueError):
    '''Raised when an order cannot be reconstructed or generated faithfully.'''


def _call_setters_with_values(order, builder):
    '''
    For each field in ``_FIELDS_AND_SETTERS``, if it exists in the order
    object, pass its value to the appropriate setter on the order builder.
    '''
    for field_name, setter_name, enum_class in _FIELDS_AND_SETTERS:
        try:
            value = order[field_name]
        except KeyError:
            continue

        if enum_class:
            try:
                value = enum_class[value]
            except (KeyError, TypeError):
                raise UnrepeatableOrderError(
                        'cannot repeat order with unsupported value {!r} for '
                        'field {!r}'.format(value, field_name)) from None

        setter = getattr(builder, setter_name)
        setter(value)


# Top-level fields
_FIELDS_AND_SETTERS = (
    ('session', 'set_session', schwab.orders.common.Session),
    ('duration', 'set_duration', schwab.orders.common.Duration),
    ('orderType', 'set_order_type', schwab.orders.common.OrderType),
    ('cancelTime', 'set_cancel_time', None),
    ('complexOrderStrategyType', 'set_complex_order_strategy_type',
        schwab.orders.common.ComplexOrderStrategyType),
    ('quantity', 'set_quantity', None),
    ('destinationLinkName', 'set_destination_link_name', None),
    ('releaseTime', 'set_release_time', None),
    ('stopPrice', 'copy_stop_price', None),
    ('stopPriceLinkBasis', 'set_stop_price_link_basis',
        schwab.orders.common.StopPriceLinkBasis),
    ('stopPriceLinkType', 'set_stop_price_link_type',
        schwab.orders.common.StopPriceLinkType),
    ('stopPriceOffset', 'set_stop_price_offset', None),
    ('stopType', 'set_stop_type', schwab.orders.common.StopType),
    ('priceLinkBasis', 'set_price_link_basis',
        schwab.orders.common.PriceLinkBasis),
    ('priceLinkType', 'set_price_link_type',
        schwab.orders.common.PriceLinkType),
    ('price', 'copy_price', None),
    ('taxLotMethod', 'set_tax_lot_method', schwab.orders.common.TaxLotMethod),
    ('activationPrice', 'set_activation_price', None),
    ('specialInstruction', 'set_special_instruction',
        schwab.orders.common.SpecialInstruction),
    ('orderStrategyType', 'set_order_strategy_type',
        schwab.orders.common.OrderStrategyType),
)

def _validate_children(strategy, children):
    if children is None:
        children = []
    if not isinstance(children, list):
        raise UnrepeatableOrderError('childOrderStrategies must be a list')
    expected = {'TRIGGER': 1, 'OCO': 2}.get(strategy, 0)
    if len(children) != expected:
        raise UnrepeatableOrderError(
            'cannot repeat {!r} order with {} children; expected {}'.format(
                strategy, len(children), expected))
    return children


def construct_repeat_order(historical_order):
    '''Reconstruct an order, rejecting unsupported child-order structures.

    TRIGGER requires one child and OCO requires two. Other strategies cannot
    have children. Unsupported shapes raise UnrepeatableOrderError.
    '''
    if 'orderStrategyType' not in historical_order:
        raise ValueError('historical order is missing orderStrategyType')
    children = _validate_children(
        historical_order['orderStrategyType'],
        historical_order.get('childOrderStrategies'))
    builder = schwab.orders.generic.OrderBuilder()

    # Top-level fields
    _call_setters_with_values(historical_order, builder)

    # Composite orders
    for child in children:
        if not isinstance(child, dict):
            raise UnrepeatableOrderError('child orders must be dictionaries')
        builder.add_child_order_strategy(construct_repeat_order(child))

    # Order legs
    if 'orderLegCollection' in historical_order:
        for leg in historical_order['orderLegCollection']:
            if leg['orderLegType'] == 'EQUITY':
                builder.add_equity_leg(
                        schwab.orders.common.EquityInstruction[leg['instruction']],
                        leg['instrument']['symbol'],
                        leg['quantity'])
            elif leg['orderLegType'] == 'OPTION':
                builder.add_option_leg(
                        schwab.orders.common.OptionInstruction[leg['instruction']],
                        leg['instrument']['symbol'],
                        leg['quantity'])
            else:
                raise ValueError(
                        'unknown orderLegType {}'.format(leg['orderLegType']))

    return builder


################################################################################
# AST generation


def code_for_builder(builder, var_name=None):
    '''
    Returns code that can be executed to construct the given builder, including
    import statements.

    :param builder: :class:`~schwab.orders.generic.OrderBuilder` to generate.
    :param var_name: If set, emit code that assigns the builder to a variable
                     with this name.
    Numeric prices retain their numeric-price behavior when the generated
    builder is built or modified.
    :raises UnrepeatableOrderError: If children cannot be represented by the
                                    supported TRIGGER/OCO helpers, or are not
                                    OrderBuilder instances.
    '''
    ast = construct_order_ast(builder)

    imports = defaultdict(set)
    lines = []
    ast.render(imports, lines)

    import_lines = []
    for module, names in imports.items():
        line = 'from {} import {}'.format(
                module, ', '.join(names))
        if len(line) > 80:
            line = 'from {} import (\n{}\n)'.format(
                    module, ',\n'.join(names))
        import_lines.append(line)

    if var_name:
        var_prefix = f'{var_name} = '
    else:
        var_prefix = ''

    return autopep8.fix_code(
            '\n'.join(import_lines) + 
            '\n\n' +
            var_prefix +
            '\n'.join(lines))


class FirstTriggersSecondAST:
    def __init__(self, first, second):
        self.first = first
        self.second = second

    def render(self, imports, lines, paren_depth=0):
        imports['schwab.orders.common'].add('first_triggers_second')

        lines.append('first_triggers_second(')
        self.first.render(imports, lines, paren_depth + 1)
        lines[-1] += ','
        self.second.render(imports, lines, paren_depth + 2)
        lines.append(')')


class OneCancelsOtherAST:
    def __init__(self, one, other):
        self.one = one
        self.other = other

    def render(self, imports, lines, paren_depth=0):
        imports['schwab.orders.common'].add('one_cancels_other')

        lines.append('one_cancels_other(')
        self.one.render(imports, lines, paren_depth + 1)
        lines[-1] += ','
        self.other.render(imports, lines, paren_depth + 2)
        lines.append(')')


class FieldAST:
    def __init__(self, setter_name, enum_type, value, *, numeric_price=False):
        self.setter_name = setter_name
        self.enum_type = enum_type
        self.value = value
        self.numeric_price = numeric_price

    def render(self, imports, lines, paren_depth=0):
        value = self.value
        if self.numeric_price:
            imports['decimal'].add('Decimal')
            value = 'Decimal({!r})'.format(value)
        elif self.enum_type:
            imports[self.enum_type.__module__].add(self.enum_type.__qualname__)
            value = self.enum_type.__qualname__ + '.' + value
        elif isinstance(value, str):
            value = repr(value)

        lines.append(f'.{self.setter_name}({value})')


class EquityOrderLegAST:
    def __init__(self, instruction, symbol, quantity):
        self.instruction = instruction
        self.symbol = symbol
        self.quantity = quantity

    def render(self, imports, lines, paren_depth=0):
        imports['schwab.orders.common'].add('EquityInstruction')
        lines.append('.add_equity_leg(EquityInstruction.{}, {!r}, {})'.format(
            self.instruction, self.symbol, self.quantity))


class OptionOrderLegAST:
    def __init__(self, instruction, symbol, quantity):
        self.instruction = instruction
        self.symbol = symbol
        self.quantity = quantity

    def render(self, imports, lines, paren_depth=0):
        imports['schwab.orders.common'].add('OptionInstruction')
        lines.append('.add_option_leg(OptionInstruction.{}, {!r}, {})'.format(
            self.instruction, self.symbol, self.quantity))


class GenericBuilderAST:
    def __init__(self, builder):
        self.top_level_fields = []
        for name, setter, enum_type in sorted(_FIELDS_AND_SETTERS):
            value = getattr(builder, '_'+name)
            if value is not None:
                numeric_price = name in builder._numeric_prices
                if numeric_price:
                    setter = {'price': 'set_price',
                              'stopPrice': 'set_stop_price'}[name]
                self.top_level_fields.append(FieldAST(
                    setter, enum_type, value, numeric_price=numeric_price))

        for leg in builder._orderLegCollection or []:
            if leg['instrument']._assetType == 'EQUITY':
                self.top_level_fields.append(EquityOrderLegAST(
                    leg['instruction'], leg['instrument']._symbol, 
                    leg['quantity']))
            elif leg['instrument']._assetType == 'OPTION':
                self.top_level_fields.append(OptionOrderLegAST(
                    leg['instruction'], leg['instrument']._symbol, 
                    leg['quantity']))
            else:
                raise ValueError('unknown leg asset type {}'.format(
                    leg['instrument']._assetType))

    def render(self, imports, lines, paren_depth=0):
        imports['schwab.orders.generic'].add('OrderBuilder')

        lines.append('OrderBuilder()')
        if self.top_level_fields:
            lines[-1] += ' \\'

        for idx, field in enumerate(self.top_level_fields):
            field.render(imports, lines, paren_depth)

            if paren_depth == 0 and idx != len(self.top_level_fields) - 1:
                lines[-1] += ' \\'


def construct_order_ast(builder):
    if not isinstance(builder, schwab.orders.generic.OrderBuilder):
        raise UnrepeatableOrderError(
            'code generation requires OrderBuilder children')
    _validate_children(
        builder._orderStrategyType, builder._childOrderStrategies)
    if builder._orderStrategyType == 'OCO':
        return OneCancelsOtherAST(
                construct_order_ast(builder._childOrderStrategies[0]),
                construct_order_ast(builder._childOrderStrategies[1]))
    elif builder._orderStrategyType == 'TRIGGER':
        return FirstTriggersSecondAST(
                GenericBuilderAST(builder),
                construct_order_ast(builder._childOrderStrategies[0]))
    else:
        return GenericBuilderAST(builder)
