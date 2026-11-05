import json
import unittest
import sys
from decimal import Decimal

from schwab.contrib.orders import (
        UnrepeatableOrderError,
        code_for_builder,
        construct_repeat_order,
)
from schwab.orders.common import (
        EquityInstruction, OptionInstruction, OrderType, first_triggers_second)
from schwab.orders.generic import OrderBuilder


class GeneratedNumericPriceTest(unittest.TestCase):
    def rebuild(self, builder):
        namespace = {}
        exec(code_for_builder(builder, 'order'), namespace)
        return namespace['order']

    def option(self):
        return OrderBuilder().add_option_leg(
            OptionInstruction.BUY_TO_OPEN, 'SYNTHETIC', 1)

    def test_numeric_option_prices_and_stops_retain_build_behavior(self):
        for setter, key in (('set_price', 'price'),
                            ('set_stop_price', 'stopPrice')):
            for value in (Decimal('0.1234'), Decimal('0.005')):
                with self.subTest(setter=setter, value=value):
                    original = getattr(self.option(), setter)(value)
                    rebuilt = self.rebuild(original)
                    if value == Decimal('0.005'):
                        with self.assertRaisesRegex(ValueError, 'truncate to zero'):
                            original.build()
                        with self.assertRaisesRegex(ValueError, 'truncate to zero'):
                            rebuilt.build()
                    else:
                        self.assertEqual(original.build(), rebuilt.build())
                        self.assertEqual('0.12', rebuilt.build()[key])

    def test_explicit_string_prices_keep_their_precision(self):
        original = self.option().set_price('0.1234').set_stop_price('0.005')
        self.assertEqual(original.build(), self.rebuild(original).build())
        self.assertEqual('0.1234', self.rebuild(original).build()['price'])

    def test_numeric_price_stays_numeric_after_later_leg_changes(self):
        original = OrderBuilder().set_price(Decimal('0.1234'))
        rebuilt = self.rebuild(original)
        for builder in (original, rebuilt):
            builder.add_option_leg(OptionInstruction.BUY_TO_OPEN, 'SYNTHETIC', 1)
        self.assertEqual(original.build(), rebuilt.build())
        self.assertEqual('0.12', rebuilt.build()['price'])

    def test_nested_option_order_retains_numeric_and_explicit_prices(self):
        child = self.option().set_price(Decimal('0.1234')).set_stop_price('0.005')
        parent = first_triggers_second(OrderBuilder(), child)
        self.assertEqual(parent.build(), self.rebuild(parent).build())
        rebuilt_child = self.rebuild(parent).build()['childOrderStrategies'][0]
        self.assertEqual('0.12', rebuilt_child['price'])
        self.assertEqual('0.005', rebuilt_child['stopPrice'])


class ConstructRepeatOrderTest(unittest.TestCase):

    def test_generated_symbols_round_trip_as_data(self):
        symbols = ['A"B', "A'B", 'A\\nB', 'A\nB',
                   'A\"); injected = True; #', '\u00e9']
        for symbol in symbols:
            for method, instruction in (
                    ('add_equity_leg', EquityInstruction.BUY),
                    ('add_option_leg', OptionInstruction.BUY_TO_OPEN)):
                with self.subTest(symbol=symbol, method=method):
                    builder = OrderBuilder()
                    getattr(builder, method)(instruction, symbol, 1)
                    namespace = {}
                    exec(code_for_builder(builder, 'order'), namespace)
                    self.assertEqual(builder.build(), namespace['order'].build())
                    self.assertNotIn('injected', namespace)

    def setUp(self):
        self.maxDiff = None

    def assertBuilder(self, expected_json, builder):
        self.assertEqual(
                json.dumps(expected_json, indent=4, sort_keys=True),
                json.dumps(builder.build(), indent=4, sort_keys=True))

        def validate_syntax(code, globalz):
            split_code = code.split('\n')
            line_format = (
                    ' {' + ':{}d'.format(len(str(len(split_code)))) + '}   {}')
            print('Generated code:')
            print()
            print('\n'.join(line_format.format(line_num + 1, line)
                for line_num, line in enumerate(split_code)))
            try:
                exec(code, globalz)
            except SyntaxError as e:
                print()
                print(e)
                assert False, 'Syntax error from generated code'

        # With a variable name, validate the syntax and expect the output
        code = code_for_builder(builder, 'test_builder')
        globalz = {}
        validate_syntax(code, globalz)
        self.assertEqual(
                json.dumps(expected_json, indent=4, sort_keys=True),
                json.dumps(
                    globalz['test_builder'].build(), indent=4, sort_keys=True))

        # With no variable name, just validate the syntax
        code = code_for_builder(builder)
        validate_syntax(code, {})


    def test_market_equity_order(self):
        historical_order = json.loads('''{
            "session": "NORMAL",
            "duration": "DAY",
            "orderType": "MARKET",
            "complexOrderStrategyType": "NONE",
            "quantity": 1.0,
            "filledQuantity": 1.0,
            "remainingQuantity": 0.0,'''+
            # XXX: See comment in contrib.orders._FIELDS_AND_SETTERS
            #"destinationLinkName": "AUTO",
            '''"orderLegCollection": [
                {
                    "orderLegType": "EQUITY",
                    "legId": 1,
                    "instrument": {
                        "assetType": "EQUITY",
                        "cusip": "1234567890",
                        "symbol": "FAKE"
                    },
                    "instruction": "BUY",
                    "positionEffect": "OPENING",
                    "quantity": 1.0
                }
            ],
            "orderStrategyType": "SINGLE",
            "orderId": 987654321,
            "cancelable": false,
            "editable": false,
            "status": "FILLED",
            "enteredTime": "2021-01-01T12:01:00+0000",
            "closeTime": "2021-01-01T12:01:01+0000",
            "tag": "tag",
            "accountId": 19191919,
            "orderActivityCollection": [
                {
                    "activityType": "EXECUTION",
                    "executionType": "FILL",
                    "quantity": 1.0,
                    "orderRemainingQuantity": 0.0,
                    "executionLegs": [
                        {
                            "legId": 1,
                            "quantity": 1.0,
                            "mismarkedQuantity": 0.0,
                            "price": 999.99,
                            "time": "2021-01-01T12:01:01+0000"
                        }
                    ]
                }
            ]
        }''')

        repeat_order = construct_repeat_order(historical_order)

        self.assertBuilder({
            'session': 'NORMAL',
            'duration': 'DAY',
            'orderType': 'MARKET',
            'complexOrderStrategyType': 'NONE',
            'quantity': 1.0,
            # 'destinationLinkName': 'AUTO',
            'orderStrategyType': 'SINGLE',
            'orderLegCollection': [{
                'instruction': 'BUY',
                'instrument': {
                    'assetType': 'EQUITY',
                    'symbol': 'FAKE'
                },
                'quantity': 1.0
            }]
        }, repeat_order)


    def test_market_order_without_legs(self):
        builder = OrderBuilder().set_order_type(OrderType.MARKET)

        self.assertBuilder({'orderType': 'MARKET'}, builder)


    def test_builder_with_cleared_legs(self):
        builder = (
            OrderBuilder()
            .set_order_type(OrderType.MARKET)
            .add_equity_leg(EquityInstruction.BUY, 'AAPL', 1)
            .clear_order_legs())

        self.assertBuilder({'orderType': 'MARKET'}, builder)


    def test_empty_builder(self):
        self.assertBuilder({}, OrderBuilder())


    def test_unknown_response_enum_has_descriptive_error(self):
        for field_name in ('duration', 'orderType'):
            historical_order = {
                'duration': 'DAY',
                'orderType': 'MARKET',
                'orderStrategyType': 'SINGLE',
            }
            historical_order[field_name] = 'UNKNOWN'

            with self.subTest(field_name=field_name):
                with self.assertRaises(UnrepeatableOrderError) as error:
                    construct_repeat_order(historical_order)

                self.assertIn("'UNKNOWN'", str(error.exception))
                self.assertIn(repr(field_name), str(error.exception))


    def test_missing_orderStrategyType(self):
        historical_order = json.loads('''{
            "session": "NORMAL",
            "duration": "DAY",
            "orderType": "MARKET",
            "complexOrderStrategyType": "NONE",
            "quantity": 1.0,
            "filledQuantity": 1.0,
            "remainingQuantity": 0.0,
            "destinationLinkName": "AUTO",
            "orderLegCollection": [
                {
                    "orderLegType": "EQUITY",
                    "legId": 1,
                    "instrument": {
                        "assetType": "EQUITY",
                        "cusip": "1234567890",
                        "symbol": "FAKE"
                    },
                    "instruction": "BUY",
                    "positionEffect": "OPENING",
                    "quantity": 1.0
                }
            ],
            "orderId": 987654321,
            "cancelable": false,
            "editable": false,
            "status": "FILLED",
            "enteredTime": "2021-01-01T12:01:00+0000",
            "closeTime": "2021-01-01T12:01:01+0000",
            "tag": "tag",
            "accountId": 19191919,
            "orderActivityCollection": [
                {
                    "activityType": "EXECUTION",
                    "executionType": "FILL",
                    "quantity": 1.0,
                    "orderRemainingQuantity": 0.0,
                    "executionLegs": [
                        {
                            "legId": 1,
                            "quantity": 1.0,
                            "mismarkedQuantity": 0.0,
                            "price": 999.99,
                            "time": "2021-01-01T12:01:01+0000"
                        }
                    ]
                }
            ]
        }''')

        with self.assertRaises(ValueError,
                msg='historical order is missing orderStrategyType'):
            construct_repeat_order(historical_order)


    def test_unknown_orderLegType(self):
        historical_order = json.loads('''{
            "session": "NORMAL",
            "duration": "DAY",
            "orderType": "MARKET",
            "complexOrderStrategyType": "NONE",
            "quantity": 1.0,
            "filledQuantity": 1.0,
            "remainingQuantity": 0.0,
            "destinationLinkName": "AUTO",
            "orderLegCollection": [
                {
                    "orderLegType": "BOGUS",
                    "legId": 1,
                    "instrument": {
                        "assetType": "EQUITY",
                        "cusip": "1234567890",
                        "symbol": "FAKE"
                    },
                    "instruction": "BUY",
                    "positionEffect": "OPENING",
                    "quantity": 1.0
                }
            ],
            "orderStrategyType": "SINGLE",
            "orderId": 987654321,
            "cancelable": false,
            "editable": false,
            "status": "FILLED",
            "enteredTime": "2021-01-01T12:01:00+0000",
            "closeTime": "2021-01-01T12:01:01+0000",
            "tag": "tag",
            "accountId": 19191919,
            "orderActivityCollection": [
                {
                    "activityType": "EXECUTION",
                    "executionType": "FILL",
                    "quantity": 1.0,
                    "orderRemainingQuantity": 0.0,
                    "executionLegs": [
                        {
                            "legId": 1,
                            "quantity": 1.0,
                            "mismarkedQuantity": 0.0,
                            "price": 999.99,
                            "time": "2021-01-01T12:01:01+0000"
                        }
                    ]
                }
            ]
        }''')

        with self.assertRaises(ValueError,
                msg='unknown orderLegType'):
            construct_repeat_order(historical_order)

    def test_unknown_orderLegType_codegen(self):
        historical_order = json.loads('''{
            "session": "NORMAL",
            "duration": "DAY",
            "orderType": "MARKET",
            "complexOrderStrategyType": "NONE",
            "quantity": 1.0,
            "filledQuantity": 1.0,
            "remainingQuantity": 0.0,
            "destinationLinkName": "AUTO",
            "orderLegCollection": [
                {
                    "orderLegType": "EQUITY",
                    "legId": 1,
                    "instrument": {
                        "assetType": "EQUITY",
                        "cusip": "1234567890",
                        "symbol": "FAKE"
                    },
                    "instruction": "BUY",
                    "positionEffect": "OPENING",
                    "quantity": 1.0
                }
            ],
            "orderStrategyType": "SINGLE",
            "orderId": 987654321,
            "cancelable": false,
            "editable": false,
            "status": "FILLED",
            "enteredTime": "2021-01-01T12:01:00+0000",
            "closeTime": "2021-01-01T12:01:01+0000",
            "tag": "tag",
            "accountId": 19191919,
            "orderActivityCollection": [
                {
                    "activityType": "EXECUTION",
                    "executionType": "FILL",
                    "quantity": 1.0,
                    "orderRemainingQuantity": 0.0,
                    "executionLegs": [
                        {
                            "legId": 1,
                            "quantity": 1.0,
                            "mismarkedQuantity": 0.0,
                            "price": 999.99,
                            "time": "2021-01-01T12:01:01+0000"
                        }
                    ]
                }
            ]
        }''')

        repeat_order = construct_repeat_order(historical_order)
        repeat_order._orderLegCollection[0]['instrument']._assetType = 'BOGUS'

        with self.assertRaises(ValueError, msg='unknown leg asset type'):
            code_for_builder(repeat_order)


    def test_limit_options_order(self):
        historical_order = json.loads('''{
            "session": "NORMAL",
            "duration": "DAY",
            "orderType": "LIMIT",
            "complexOrderStrategyType": "NONE",
            "quantity": 1.0,
            "filledQuantity": 1.0,
            "remainingQuantity": 0.0,'''+
            # XXX: See comment in contrib.orders._FIELDS_AND_SETTERS
            #"destinationLinkName": "AUTO",
            '''"price": 0.21,
            "orderLegCollection": [{
                "orderLegType": "OPTION",
                "legId": 1,
                "instrument": {
                    "assetType": "OPTION",
                    "cusip": "0SPY..RJ00309000",
                    "symbol": "SPY_061920P309",
                    "description": "SPY Jun 19 2020 309.0 Put",
                    "underlyingSymbol": "SPY"
                },
                "instruction": "SELL_TO_CLOSE",
                "positionEffect": "CLOSING",
                "quantity": 1.0
            }],
            "orderStrategyType": "SINGLE",
            "orderId": 987654321,
            "cancelable": false,
            "editable": false,
            "status": "FILLED",
            "enteredTime": "2021-01-01T12:01:00+0000",
            "closeTime": "2021-01-01T12:01:01+0000",
            "tag": "tag",
            "accountId": 19191919,
            "orderActivityCollection": [{
                "activityType": "EXECUTION",
                "executionType": "FILL",
                "quantity": 1.0,
                "orderRemainingQuantity": 0.0,
                "executionLegs": [{
                    "legId": 1,
                    "quantity": 1.0,
                    "mismarkedQuantity": 0.0,
                    "price": 0.21,
                    "time": "2021-01-01T12:01:01+0000"
                }]
            }]
	}''')

        repeat_order = construct_repeat_order(historical_order)

        self.assertBuilder({
            'session': 'NORMAL',
            'duration': 'DAY',
            'orderType': 'LIMIT',
            'complexOrderStrategyType': 'NONE',
            'quantity': 1.0,
            #'destinationLinkName': 'AUTO',
            'price': 0.21,
            'orderStrategyType': 'SINGLE',
            'orderLegCollection': [{
                'instruction': 'SELL_TO_CLOSE',
                'instrument': {
                    'assetType': 'OPTION',
                    'symbol': 'SPY_061920P309'
                },
                'quantity': 1.0
            }]
        }, repeat_order)


    def test_complex_options_order(self):
        historical_order = json.loads('''{
            "session": "NORMAL",
            "duration": "DAY",
            "orderType": "NET_DEBIT",
            "complexOrderStrategyType": "BUTTERFLY",
            "quantity": 1.0,
            "filledQuantity": 1.0,
            "remainingQuantity": 0.0,'''+
            # XXX: See comment in contrib.orders._FIELDS_AND_SETTERS
            '''"destinationLinkName": "AUTO",
            "price": 0.03,
            "orderLegCollection": [
                {
                    "orderLegType": "OPTION",
                    "legId": 1,
                    "instrument": {
                        "assetType": "OPTION",
                        "cusip": "0SPY..F110409000",
                        "symbol": "SPY_060121C409",
                        "description": "SPY JUN 1 2021 409.0 Call"
                    },
                    "instruction": "BUY_TO_OPEN",
                    "positionEffect": "OPENING",
                    "quantity": 1.0
                },
                {
                    "orderLegType": "OPTION",
                    "legId": 2,
                    "instrument": {
                        "assetType": "OPTION",
                        "cusip": "0SPY..F110410000",
                        "symbol": "SPY_060121C410",
                        "description": "SPY JUN 1 2021 410.0 Call"
                    },
                    "instruction": "SELL_TO_OPEN",
                    "positionEffect": "OPENING",
                    "quantity": 2.0
                },
                {
                    "orderLegType": "OPTION",
                    "legId": 3,
                    "instrument": {
                        "assetType": "OPTION",
                        "cusip": "0SPY..F110411000",
                        "symbol": "SPY_060121C411",
                        "description": "SPY JUN 1 2021 411.0 Call"
                    },
                    "instruction": "BUY_TO_OPEN",
                    "positionEffect": "OPENING",
                    "quantity": 1.0
                }
            ],
            "orderStrategyType": "SINGLE",
            "orderId": 1919191919,
            "cancelable": false,
            "editable": false,
            "status": "FILLED",
            "enteredTime": "2021-05-12T14:39:58+0000",
            "closeTime": "2021-05-12T14:39:58+0000",
            "accountId": 700000007,
            "orderActivityCollection": [
                {
                    "activityType": "EXECUTION",
                    "executionType": "FILL",
                    "quantity": 1.0,
                    "orderRemainingQuantity": 0.0,
                    "executionLegs": [
                        {
                            "legId": 1,
                            "quantity": 1.0,
                            "mismarkedQuantity": 0.0,
                            "price": 8.24,
                            "time": "2021-05-12T14:39:58+0000"
                        },
                        {
                            "legId": 2,
                            "quantity": 2.0,
                            "mismarkedQuantity": 0.0,
                            "price": 7.585,
                            "time": "2021-05-12T14:39:58+0000"
                        },
                        {
                            "legId": 3,
                            "quantity": 1.0,
                            "mismarkedQuantity": 0.0,
                            "price": 6.96,
                            "time": "2021-05-12T14:39:58+0000"
                        }
                    ]
                }
            ]
        }
	''')

        repeat_order = construct_repeat_order(historical_order)

        self.assertBuilder({
            'session': 'NORMAL',
            'duration': 'DAY',
            'orderType': 'NET_DEBIT',
            'complexOrderStrategyType': 'BUTTERFLY',
            'quantity': 1.0,
            'destinationLinkName': 'AUTO',
            'price': 0.03,
            'orderStrategyType': 'SINGLE',
            'orderLegCollection': [{
                'instruction': 'BUY_TO_OPEN',
                'instrument': {
                    'assetType': 'OPTION',
                    'symbol': 'SPY_060121C409'
                },
                'quantity': 1.0
            }, {
                'instruction': 'SELL_TO_OPEN',
                'instrument': {
                    'assetType': 'OPTION',
                    'symbol': 'SPY_060121C410'
                },
                'quantity': 2.0
            }, {
                'instruction': 'BUY_TO_OPEN',
                'instrument': {
                    'assetType': 'OPTION',
                    'symbol': 'SPY_060121C411'
                },
                'quantity': 1.0
            }]
        }, repeat_order)


    def test_one_triggers_other(self):
        historical_order = json.loads('''{
            "session": "NORMAL",
            "duration": "GOOD_TILL_CANCEL",
            "orderType": "LIMIT",
            "complexOrderStrategyType": "NONE",
            "quantity": 2.0,
            "filledQuantity": 2.0,
            "remainingQuantity": 0.0,'''+
            # XXX: See comment in contrib.orders._FIELDS_AND_SETTERS
            #"destinationLinkName": "AUTO",
            '''"price": 3.6,
            "orderLegCollection": [
                {
                    "orderLegType": "OPTION",
                    "legId": 1,
                    "instrument": {
                        "assetType": "OPTION",
                        "cusip": "0AEO..HK10035000",
                        "symbol": "AEO_082021C35",
                        "description": "AEO AUG 20 2021 35.0 Call"
                    },
                    "instruction": "BUY_TO_OPEN",
                    "positionEffect": "OPENING",
                    "quantity": 2.0
                }
            ],
            "orderStrategyType": "TRIGGER",
            "orderId": 29292929,
            "cancelable": false,
            "editable": false,
            "status": "FILLED",
            "enteredTime": "2021-04-20T02:40:28+0000",
            "closeTime": "2021-04-20T13:31:53+0000",
            "accountId": 19191919,
            "orderActivityCollection": [
                {
                    "activityType": "EXECUTION",
                    "executionType": "FILL",
                    "quantity": 2.0,
                    "orderRemainingQuantity": 0.0,
                    "executionLegs": [
                        {
                            "legId": 1,
                            "quantity": 2.0,
                            "mismarkedQuantity": 0.0,
                            "price": 3.6,
                            "time": "2021-04-20T13:31:53+0000"
                        }
                    ]
                }
            ],
            "childOrderStrategies": [
                {
                    "session": "NORMAL",
                    "duration": "GOOD_TILL_CANCEL",
                    "orderType": "LIMIT",
                    "complexOrderStrategyType": "NONE",
                    "quantity": 2.0,
                    "filledQuantity": 2.0,
                    "remainingQuantity": 0.0,'''+
                    #"destinationLinkName": "NYSE",
                    '''"price": 3.7,
                    "orderLegCollection": [
                        {
                            "orderLegType": "OPTION",
                            "legId": 1,
                            "instrument": {
                                "assetType": "OPTION",
                                "cusip": "0AEO..HK10035000",
                                "symbol": "AEO_082021C35",
                                "description": "AEO AUG 20 2021 35.0 Call"
                            },
                            "instruction": "SELL_TO_CLOSE",
                            "positionEffect": "CLOSING",
                            "quantity": 2.0
                        }
                    ],
                    "orderStrategyType": "SINGLE",
                    "orderId": 22992992,
                    "cancelable": false,
                    "editable": false,
                    "status": "FILLED",
                    "enteredTime": "2021-04-20T02:40:28+0000",
                    "closeTime": "2021-04-29T15:02:53+0000",
                    "accountId": 19191919,
                    "orderActivityCollection": [
                        {
                            "activityType": "EXECUTION",
                            "executionType": "FILL",
                            "quantity": 2.0,
                            "orderRemainingQuantity": 0.0,
                            "executionLegs": [
                                {
                                    "legId": 1,
                                    "quantity": 2.0,
                                    "mismarkedQuantity": 0.0,
                                    "price": 3.7,
                                    "time": "2021-04-29T15:02:53+0000"
                                }
                            ]
                        }
                    ]
                }
            ]
        }''')

        repeat_order = construct_repeat_order(historical_order)

        self.assertBuilder({
            'session': 'NORMAL',
            'duration': 'GOOD_TILL_CANCEL',
            'orderType': 'LIMIT',
            'complexOrderStrategyType': 'NONE',
            'quantity': 2.0,
            #'destinationLinkName': 'AUTO',
            'orderStrategyType': 'TRIGGER',
            'price': 3.6,
            'orderLegCollection': [{
                'instruction': 'BUY_TO_OPEN',
                'instrument': {
                    'assetType': 'OPTION',
                    'symbol': 'AEO_082021C35'
                },
                'quantity': 2.0,
            }],
            'childOrderStrategies': [{
                'session': 'NORMAL',
                'duration': 'GOOD_TILL_CANCEL',
                'orderType': 'LIMIT',
                'complexOrderStrategyType': 'NONE',
                'quantity': 2.0,
                'price': 3.7,
                #'destinationLinkName': 'NYSE',
                'orderStrategyType': 'SINGLE',
                'orderLegCollection': [{
                    'instruction': 'SELL_TO_CLOSE',
                    'instrument': {
                        'assetType': 'OPTION',
                        'symbol': 'AEO_082021C35'
                    },
                    'quantity': 2.0,
                }]
            }]
        }, repeat_order)

    def test_oco_inside_oto(self):
        historical_order = json.loads('''{
            "session": "NORMAL",
            "duration": "DAY",
            "orderType": "LIMIT",
            "complexOrderStrategyType": "NONE",
            "quantity": 1.0,
            "filledQuantity": 0.0,
            "remainingQuantity": 1.0,'''+
            # XXX: See comment in contrib.orders._FIELDS_AND_SETTERS
            # "destinationLinkName": "AUTO",
            '''"price": 2.71,
            "orderLegCollection": [
                {
                    "orderLegType": "OPTION",
                    "legId": 1,
                    "instrument": {
                        "assetType": "OPTION",
                        "cusip": "0BIGC.JF10060000",
                        "symbol": "BIGC_101521C60",
                        "description": "BIGC OCT 15 2021 60.0 Call"
                    },
                    "instruction": "BUY_TO_OPEN",
                    "positionEffect": "OPENING",
                    "quantity": 1.0
                }
            ],
            "orderStrategyType": "TRIGGER",
            "orderId": 4403477551,
            "cancelable": true,
            "editable": false,
            "status": "QUEUED",
            "enteredTime": "2021-05-13T03:12:54+0000",
            "accountId": 123123123,
            "childOrderStrategies": [
                {
                    "orderStrategyType": "OCO",
                    "orderId": 4403477554,
                    "cancelable": true,
                    "editable": false,
                    "accountId": 123123123,
                    "childOrderStrategies": [
                        {
                            "session": "NORMAL",
                            "duration": "DAY",
                            "orderType": "LIMIT",
                            "complexOrderStrategyType": "NONE",
                            "quantity": 1.0,
                            "filledQuantity": 0.0,
                            "remainingQuantity": 1.0,'''+
                            # "destinationLinkName": "AUTO",
                            '''"orderLegCollection": [
                                {
                                    "orderLegType": "OPTION",
                                    "legId": 1,
                                    "instrument": {
                                        "assetType": "OPTION",
                                        "cusip": "0BIGC.JF10060000",
                                        "symbol": "BIGC_101521C60",
                                        "description": "BIGC OCT 15 2021 60.0 Call"
                                    },
                                    "instruction": "SELL_TO_CLOSE",
                                    "positionEffect": "CLOSING",
                                    "quantity": 1.0
                                }
                            ],
                            "orderStrategyType": "SINGLE",
                            "orderId": 4403477553,
                            "cancelable": true,
                            "editable": false,
                            "status": "ACCEPTED",
                            "enteredTime": "2021-05-13T03:12:54+0000",
                            "accountId": 12312312
                        },
                        {
                            "session": "NORMAL",
                            "duration": "DAY",
                            "orderType": "STOP",
                            "complexOrderStrategyType": "NONE",
                            "quantity": 1.0,
                            "filledQuantity": 0.0,
                            "remainingQuantity": 1.0,'''+
                            # "destinationLinkName": "AUTO",
                            '''"orderLegCollection": [
                                {
                                    "orderLegType": "OPTION",
                                    "legId": 1,
                                    "instrument": {
                                        "assetType": "OPTION",
                                        "cusip": "0BIGC.JF10060000",
                                        "symbol": "BIGC_101521C60",
                                        "description": "BIGC OCT 15 2021 60.0 Call"
                                    },
                                    "instruction": "SELL_TO_CLOSE",
                                    "positionEffect": "CLOSING",
                                    "quantity": 1.0
                                }
                            ],
                            "orderStrategyType": "SINGLE",
                            "orderId": 4403477554,
                            "cancelable": true,
                            "editable": false,
                            "status": "ACCEPTED",
                            "enteredTime": "2021-05-13T03:12:54+0000",
                            "accountId": 488435533
                        }
                    ]
                }
            ]
        }''')

        repeat_order = construct_repeat_order(historical_order)

        self.assertBuilder({
            'session': 'NORMAL',
            'duration': 'DAY',
            'orderType': 'LIMIT',
            'complexOrderStrategyType': 'NONE',
            'quantity': 1.0,
            #'destinationLinkName': 'AUTO',
            'orderStrategyType': 'TRIGGER',
            'price': 2.71,
            'orderLegCollection': [{
                'instruction': 'BUY_TO_OPEN',
                'instrument': {
                    'assetType': 'OPTION',
                    'symbol': 'BIGC_101521C60'
                },
                'quantity': 1.0,
            }],
            'childOrderStrategies': [{
                'orderStrategyType': 'OCO',
                'childOrderStrategies': [{
                    'session': 'NORMAL',
                    'duration': 'DAY',
                    'orderType': 'LIMIT',
                    'complexOrderStrategyType': 'NONE',
                    'quantity': 1.0,
                    #'destinationLinkName': 'AUTO',
                    'orderStrategyType': 'SINGLE',
                    'orderLegCollection': [{
                        'instruction': 'SELL_TO_CLOSE',
                        'instrument': {
                            'assetType': 'OPTION',
                            'symbol': 'BIGC_101521C60',
                        },
                        'quantity': 1.0,
                    }]
                }, {
                    'session': 'NORMAL',
                    'duration': 'DAY',
                    'orderType': 'STOP',
                    'complexOrderStrategyType': 'NONE',
                    'quantity': 1.0,
                    #'destinationLinkName': 'AUTO',
                    'orderStrategyType': 'SINGLE',
                    'orderLegCollection': [{
                        'instruction': 'SELL_TO_CLOSE',
                        'instrument': {
                            'assetType': 'OPTION',
                            'symbol': 'BIGC_101521C60',
                        },
                        'quantity': 1.0,
                    }]
                }]
            }]
        }, repeat_order)
