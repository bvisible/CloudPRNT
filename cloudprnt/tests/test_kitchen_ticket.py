"""The kitchen ticket of a table order: one station's lines, of one send, without prices."""

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from cloudprnt.kitchen_ticket import _lines, get_kitchen_ticket_markup, print_kitchen_ticket
from cloudprnt.labels import label_of, station_label, table_label

# POS Next names a new table and a new station by a hash, and keeps what a person reads in a label
TABLE_NAME = "k3x9q2m7ab"
STATION_NAME = "h4sh0f4stn"
LABELS = {TABLE_NAME: "Table 5", STATION_NAME: "Kitchen"}


def read_labels(doctype, label_field, name):
	return LABELS.get(name, name)


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

	def test_the_cook_reads_the_label_of_the_table_and_of_the_station_not_their_name(self):
		doc = order()
		doc.restaurant_table = TABLE_NAME
		for ticket_line in doc.items:
			if ticket_line.preparation_station == "Kitchen":
				ticket_line.preparation_station = STATION_NAME
		with patch("cloudprnt.labels.label_of", read_labels):
			markup = get_kitchen_ticket_markup(doc, STATION_NAME, _lines(doc, STATION_NAME, None))
		self.assertIn("Table 5", markup)
		self.assertIn("KITCHEN", markup)
		self.assertNotIn(TABLE_NAME, markup)
		self.assertNotIn(STATION_NAME.upper(), markup)

	def test_a_ticket_of_a_table_and_a_station_that_have_no_label_reads_their_name(self):
		doc = order()
		with patch("cloudprnt.labels.label_of", read_labels):
			markup = get_kitchen_ticket_markup(doc, "Kitchen", _lines(doc, "Kitchen", None))
		self.assertIn("Table 5", markup)
		self.assertIn("KITCHEN", markup)

	def test_a_station_without_a_printer_is_named_by_its_label_in_the_refusal(self):
		doc = order()
		doc.restaurant_table = TABLE_NAME
		for ticket_line in doc.items:
			ticket_line.preparation_station = STATION_NAME
		with (
			patch("cloudprnt.kitchen_ticket.guards.require"),
			patch.object(frappe.db, "exists", return_value=True),
			patch.object(frappe, "get_doc", return_value=doc),
			patch("cloudprnt.kitchen_ticket._printer_for_station", return_value=None),
			patch("cloudprnt.labels.label_of", read_labels),
			self.assertRaises(frappe.ValidationError) as refused,
		):
			print_kitchen_ticket(doc.name, station=STATION_NAME)
		self.assertIn("Kitchen", str(refused.exception))
		self.assertNotIn(STATION_NAME, str(refused.exception))

	def test_the_answer_names_each_ticket_station_by_its_name_and_by_its_label(self):
		doc = order()
		doc.restaurant_table = TABLE_NAME
		for ticket_line in doc.items:
			ticket_line.preparation_station = STATION_NAME
		printer = SimpleNamespace(paper_width=80, label="Pass")
		with (
			patch("cloudprnt.kitchen_ticket.guards.require"),
			patch.object(frappe.db, "exists", return_value=True),
			patch.object(frappe, "get_doc", return_value=doc),
			patch("cloudprnt.kitchen_ticket._printer_for_station", return_value=printer),
			patch("cloudprnt.kitchen_ticket.jobs.enqueue", return_value={"job": "JOB-1"}),
			patch("cloudprnt.labels.label_of", read_labels),
		):
			answer = print_kitchen_ticket(doc.name, station=STATION_NAME)
		self.assertEqual(
			[(t["station"], t["station_name"], t["printer"]) for t in answer["tickets"]],
			[(STATION_NAME, "Kitchen", "Pass")],
		)


class TestLabels(FrappeTestCase):
	def test_a_record_is_read_by_its_label(self):
		with (
			patch.object(frappe.db, "exists", return_value=True),
			patch.object(frappe.db, "has_column", return_value=True),
			patch.object(frappe.db, "get_value", return_value="Table 5") as found,
		):
			self.assertEqual(table_label(TABLE_NAME), "Table 5")
		found.assert_called_once_with("Restaurant Table", TABLE_NAME, "table_name")

	def test_a_station_is_read_by_its_label(self):
		with (
			patch.object(frappe.db, "exists", return_value=True),
			patch.object(frappe.db, "has_column", return_value=True),
			patch.object(frappe.db, "get_value", return_value="Kitchen") as found,
		):
			self.assertEqual(station_label(STATION_NAME), "Kitchen")
		found.assert_called_once_with("Preparation Station", STATION_NAME, "station_name")

	def test_a_record_without_a_label_is_read_by_its_name(self):
		with (
			patch.object(frappe.db, "exists", return_value=True),
			patch.object(frappe.db, "has_column", return_value=True),
			patch.object(frappe.db, "get_value", return_value=None),
		):
			self.assertEqual(table_label("Table 5"), "Table 5")

	def test_without_pos_next_the_name_is_all_there_is(self):
		with (
			patch.object(frappe.db, "exists", return_value=False),
			patch.object(frappe.db, "get_value") as found,
		):
			self.assertEqual(table_label("Table 5"), "Table 5")
		found.assert_not_called()

	def test_a_pos_next_not_migrated_yet_has_no_label_to_read(self):
		with (
			patch.object(frappe.db, "exists", return_value=True),
			patch.object(frappe.db, "has_column", return_value=False),
			patch.object(frappe.db, "get_value") as found,
		):
			self.assertEqual(station_label("Kitchen"), "Kitchen")
		found.assert_not_called()

	def test_nothing_has_no_label(self):
		self.assertEqual(label_of("Restaurant Table", "table_name", None), "")
		self.assertEqual(label_of("Restaurant Table", "table_name", ""), "")
