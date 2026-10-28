"""The kitchen ticket of a table order: one station's lines, of one send, without prices."""

from types import SimpleNamespace

import frappe
from frappe.tests.utils import FrappeTestCase

from cloudprnt.kitchen_ticket import _lines, get_kitchen_ticket_markup


class Order(SimpleNamespace):
	def get(self, key, default=None):
		return getattr(self, key, default)


def line(**values):
	return frappe._dict(
		{"item_code": "x", "item_name": "x", "qty": 1, "preparation_station": "Kitchen", "kds_batch": 1, **values}
	)


def order():
	return Order(
		name="ACC-SINV-TEST-0002",
		owner="Administrator",
		restaurant_table="Table 5",
		customer_name="Walk-in",
		items=[
			line(
				item_name="Steak [rare]",
				qty=2,
				rate=38.5,
				# as POS Next keeps a line's options
				posa_item_modifiers='[{"group": "Cooking", "options": [{"name": "Medium", "price_adjustment": 0}]},'
				' {"group": "Side", "options": [{"name": "Fries", "price_adjustment": 0}, {"name": "Salad"}]}]',
				posa_special_instructions="No salt",
				kds_batch=1,
			),
			line(item_name="Wine", qty=1, preparation_station="Bar", kds_batch=1),
			line(item_name="Tiramisu", qty=1, kds_batch=2, posa_special_instructions="For the birthday"),
		],
	)


class TestKitchenTicket(FrappeTestCase):
	def test_a_ticket_holds_one_station_and_one_send(self):
		doc = order()
		self.assertEqual([l.item_name for l in _lines(doc, "Kitchen", 1)], ["Steak [rare]"])
		self.assertEqual([l.item_name for l in _lines(doc, "Kitchen", None)], ["Steak [rare]", "Tiramisu"])
		self.assertEqual([l.item_name for l in _lines(doc, "Bar", None)], ["Wine"])

	def test_the_cook_reads_the_table_the_quantities_and_the_remarks_and_no_price(self):
		doc = order()
		markup = get_kitchen_ticket_markup(doc, "Kitchen", _lines(doc, "Kitchen", None), batch=None)
		self.assertIn("Table 5", markup)
		self.assertIn("KITCHEN", markup)
		self.assertIn("2 x Steak \\[rare\\]", markup, "the brackets of a name are printed, not read as tags")
		self.assertIn("> Cooking: Medium", markup, "options are read out of POS Next's JSON")
		self.assertIn("> Side: Fries, Salad", markup)
		self.assertNotIn("price_adjustment", markup)
		self.assertIn("> No salt", markup)
		self.assertIn("> For the birthday", markup)
		self.assertNotIn("38.5", markup)
		self.assertNotIn("Wine", markup, "the bar's lines go to the bar")
		self.assertTrue(markup.rstrip().endswith("[cut: feed; partial]"))
