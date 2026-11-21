from ..utils import has_diff, no_duplicates
from schwab.orders.common import *
from schwab.orders.generic import OrderBuilder

import unittest
import json
import re
from textwrap import dedent
from pathlib import Path
from unittest.mock import Mock

class MultiOrderTest(unittest.TestCase):

    def test_documented_bracket_matches_order_specification(self):
        docs = (Path(__file__).resolve().parents[2] /
                'docs' / 'order-builder.rst').read_text(encoding='utf-8')
        bracket_docs = docs.split('For an advanced example', 1)[1]
        def block(language):
            match = re.search(r'^\.\. code-block:: ' + language + r'\n\n((?:  .*\n|\n)+)',
                              bracket_docs, flags=re.MULTILINE)
            return dedent(match.group(1))
        scope = {}
        exec(block('python'), scope)
        order = scope['bracket'].build()
        self.assertEqual(order, json.loads(block('JSON')))
        profit, stop = order['childOrderStrategies'][0]['childOrderStrategies']
        self.assertEqual((profit['price'], stop['stopPrice'], stop['price']),
                         ('1400.00', '1250.00', '1240.00'))
        self.assertEqual([leg['duration'] for leg in (order, profit, stop)],
                         ['GOOD_TILL_CANCEL'] * 3)

    def test_documented_composite_example_submits_once(self):
        docs = (Path(__file__).resolve().parents[2] /
                'docs' / 'order-templates.rst').read_text(encoding='utf-8')
        example = docs.split('.. _composite_order_example:', 1)[1]
        example = example.split('.. code-block:: python', 1)[1]
        lines = example.lstrip('\n').splitlines()
        code = []
        for line in lines:
            if line and not line.startswith('  '):
                break
            code.append(line[2:] if line.startswith('  ') else '')

        client = Mock()
        exec('\n'.join(code), {'client': client, 'account_hash': 'hash'})

        client.place_order.assert_called_once()
        account, order = client.place_order.call_args.args
        self.assertEqual(account, 'hash')
        built = order.build()
        self.assertEqual(built['orderStrategyType'], 'TRIGGER')
        self.assertEqual(len(built['childOrderStrategies']), 1)
        self.assertEqual(built['orderLegCollection'][0]['instruction'], 'BUY')
        self.assertEqual(
            built['childOrderStrategies'][0]['orderLegCollection'][0]
                 ['instruction'], 'SELL')

    def test_trigger_can_reuse_first_order(self):
        entry = OrderBuilder().set_order_strategy_type(OrderStrategyType.SINGLE)
        before = entry.build()
        first = first_triggers_second(entry, OrderBuilder().set_price('10.00'))
        second = first_triggers_second(entry, OrderBuilder().set_price('11.00'))

        self.assertIsNot(entry, first)
        self.assertIsNot(first, second)
        self.assertEqual(before, entry.build())
        self.assertEqual([{'price': '10.00'}], first.build()['childOrderStrategies'])
        self.assertEqual([{'price': '11.00'}], second.build()['childOrderStrategies'])

    def test_trigger_copies_nested_first_order_state(self):
        child = OrderBuilder().set_price('10.00')
        entry = (OrderBuilder()
                 .add_equity_leg(EquityInstruction.BUY, 'AAPL', 1)
                 .add_child_order_strategy(child))
        trigger = first_triggers_second(entry, OrderBuilder().set_price('11.00'))
        before = trigger.build()

        child.set_price('12.00')
        entry.add_equity_leg(EquityInstruction.BUY, 'MSFT', 1)
        self.assertEqual(before, trigger.build())

    @no_duplicates
    def test_oco(self):
        self.assertFalse(has_diff({
            'orderStrategyType': 'OCO',
            'childOrderStrategies': [
                {'session': 'NORMAL'},
                {'duration': 'DAY'},
            ]
        }, one_cancels_other(
            OrderBuilder().set_session(Session.NORMAL),
            OrderBuilder().set_duration(Duration.DAY)).build()))

    @no_duplicates
    def test_trigger(self):
        self.assertFalse(has_diff({
            'orderStrategyType': 'TRIGGER',
            'session': 'NORMAL',
            'childOrderStrategies': [
                {'duration': 'DAY'},
            ]
        }, first_triggers_second(
            OrderBuilder().set_session(Session.NORMAL),
            OrderBuilder().set_duration(Duration.DAY)).build()))
