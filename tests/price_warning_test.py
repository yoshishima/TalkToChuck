import unittest
import warnings

from schwab.orders.generic import OrderBuilder


class PriceWarningTest(unittest.TestCase):
    def test_float_warning_points_to_caller(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            OrderBuilder().set_price(1.5)
        self.assertEqual(1, len(caught))
        self.assertIs(caught[0].category, DeprecationWarning)
        self.assertEqual(__file__, caught[0].filename)

    def test_integer_price_does_not_warn(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            OrderBuilder().set_price(1)
        self.assertEqual([], caught)
