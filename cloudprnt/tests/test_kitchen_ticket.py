"""The kitchen ticket of a table order: one station's lines, of one send, without prices."""

import importlib.util
import json
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from cloudprnt.kitchen_ticket import _lines, get_kitchen_ticket_markup, print_kitchen_ticket
from cloudprnt.labels import label_of, room_and_digits_of, station_label, table_label, ticket_reference
from cloudprnt.tests.hostile_text import CLEAN, HOSTILE, unsafe_characters

# POS Next names a new table and a new station by a hash, and keeps what a person reads in a label
TABLE_NAME = "k3x9q2m7ab"
STATION_NAME = "h4sh0f4stn"
LABELS = {TABLE_NAME: "Table 5", STATION_NAME: "Kitchen"}


def read_labels(doctype, label_field, name):
	return LABELS.get(name, name)


def pos_next_has_room_codes():
	"""Whether POS Next, with its rule for the code of a room, is installed on this site
	(cloudprnt's own CI has none)."""
	try:
		return importlib.util.find_spec("pos_next.restaurant.room_code") is not None
	except (ImportError, ValueError):
		return False


def a_pos_next_that_codes_every_room_as(answer):
	"""A POS Next of the shape cloudprnt reads (`pos_next.restaurant.room_code.room_code`), for a site that has none.

	Returns the stand-in's module (its `room_code` is a Mock, so that a test sees what it was asked)
	and the patch that installs it."""
	module = ModuleType("pos_next.restaurant.room_code")
	module.room_code = Mock(return_value=answer)
	restaurant = ModuleType("pos_next.restaurant")
	restaurant.room_code = module
	package = ModuleType("pos_next")
	package.restaurant = restaurant
	return module, patch.dict(
		sys.modules,
		{"pos_next": package, "pos_next.restaurant": restaurant, "pos_next.restaurant.room_code": module},
	)


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

	def test_the_cooks_paper_holds_no_control_character_whatever_a_guest_or_a_waiter_typed(self):
		# A guest's QR remark is stored as typed. BEL and SUB drive the cash drawer in Star line mode, ESC @ resets the
		# printer: the kitchen's paper is as safe as the receipt's.
		doc = order()
		doc.restaurant_table = TABLE_NAME
		for ticket_line in doc.items:
			if ticket_line.preparation_station == "Kitchen":
				ticket_line.preparation_station = STATION_NAME
		doc.items[0].item_name = HOSTILE
		doc.items[0].posa_special_instructions = HOSTILE
		doc.items[0].posa_item_modifiers = json.dumps([{"group": HOSTILE, "options": [{"name": HOSTILE}]}])
		labels = {TABLE_NAME: HOSTILE, STATION_NAME: HOSTILE}
		with patch("cloudprnt.labels.label_of", lambda doctype, field, name: labels.get(name, name)):
			markup = get_kitchen_ticket_markup(doc, STATION_NAME, _lines(doc, STATION_NAME, None), title=HOSTILE)
		self.assertEqual(unsafe_characters(markup), [])
		self.assertIn(f"2 x {CLEAN}", markup)
		self.assertIn(f"> {CLEAN}: {CLEAN}", markup, "an option group and its option")
		# a remark is read line by line first (a form feed or a C1 line break starts a new row), then made safe
		for row in ("ABC@DE", "F GHIJK", "LM"):
			self.assertIn(f"\n   > {row}\n", markup, "a remark, at the normal size of the medium text")
		self.assertIn(f"[magnify: width 2; height 2][bold: on]{CLEAN}[bold: off][magnify]", markup, "the table")
		self.assertIn(CLEAN.upper(), markup, "the station")

	def test_a_name_with_a_backslash_or_a_semicolon_keeps_the_kitchens_columns(self):
		doc = order()
		doc.owner = "Administrator"
		real = frappe.db.get_value

		def get_value(doctype, *args, **kwargs):
			return "Anne\\; [x]" if doctype == "User" else real(doctype, *args, **kwargs)

		with patch.object(frappe.db, "get_value", get_value), patch("cloudprnt.labels.label_of", read_labels):
			markup = get_kitchen_ticket_markup(doc, "Kitchen", _lines(doc, "Kitchen", None))
		# a value never escapes ';' and never ends a tag; a backslash is cputil's escape, doubled it prints once
		self.assertIn("[column: left Waiter: Anne\\\\, (x); right", markup)

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
			patch("cloudprnt.kitchen_ticket.text_size_of", return_value="Medium"),
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


class TestTheReferenceOfTheOrder(FrappeTestCase):
	"""The small line under the table: the ticket number the restaurant gave the order, never the draft's invoice number."""

	def markup(self, doc, room="S01", digits=4):
		with (
			patch("cloudprnt.labels.label_of", read_labels),
			patch("cloudprnt.labels.room_and_digits_of", return_value=(room, digits)),
		):
			return get_kitchen_ticket_markup(doc, "Kitchen", _lines(doc, "Kitchen", None))

	def test_an_order_with_a_ticket_number_prints_its_room_its_table_and_its_number_not_its_invoice_number(self):
		doc = order()
		doc.posa_ticket_no = 7

		markup = self.markup(doc)

		self.assertIn("S01-Table 5-0007", markup)
		self.assertNotIn("ACC-SINV-TEST-0002", markup, "a draft takes its invoice number in an order nobody chose")

	def test_the_number_is_written_with_the_digits_of_the_restaurant(self):
		doc = order()
		doc.posa_ticket_no = 7

		self.assertIn("S01-Table 5-007", self.markup(doc, digits=3))
		self.assertIn("S01-Table 5-7", self.markup(doc, digits=1))

	def test_a_number_longer_than_its_digits_is_not_cut(self):
		doc = order()
		doc.posa_ticket_no = 12345

		self.assertIn("S01-Table 5-12345", self.markup(doc))

	def test_a_takeaway_order_prints_its_code_and_its_number(self):
		doc = order()
		doc.restaurant_table = None
		doc.is_takeaway = 1
		doc.takeaway_number = "T-004"
		doc.posa_ticket_no = 12

		markup = self.markup(doc, room="EMP")

		self.assertIn("EMP-0012", markup)
		self.assertNotIn("ACC-SINV-TEST-0002", markup)

	def test_a_room_that_has_no_code_is_left_out(self):
		doc = order()
		doc.posa_ticket_no = 7

		self.assertIn("Table 5-0007", self.markup(doc, room=""))

	def test_an_order_without_a_ticket_number_keeps_its_name(self):
		doc = order()

		markup = self.markup(doc)

		self.assertIn("ACC-SINV-TEST-0002", markup)
		self.assertNotIn("S01-", markup)

	def test_an_order_whose_ticket_number_is_zero_has_none(self):
		doc = order()
		doc.posa_ticket_no = 0

		self.assertEqual(ticket_reference(doc), "ACC-SINV-TEST-0002")


class TestWhereTheReferenceComesFrom(FrappeTestCase):
	"""The room's code and the restaurant's digits, read from POS Next's records when the site has them."""

	def lookups(self, values):
		def get_value(doctype, name, fields=None, as_dict=False, **kwargs):
			return values.get((doctype, name if not isinstance(name, dict) else tuple(sorted(name.items()))))

		return get_value

	@unittest.skipUnless(
		pos_next_has_room_codes(),
		"POS Next's rule for the code of a room is not installed here: "
		"the test below proves the hand-over without it",
	)
	def test_the_room_is_that_of_the_table_and_the_digits_those_of_its_restaurant(self):
		doc = order()
		doc.restaurant_table = TABLE_NAME
		found = {
			("Restaurant Table", TABLE_NAME): frappe._dict(area="AREA-1", restaurant="Bistro"),
			("Restaurant Area", "AREA-1"): frappe._dict(area_name="Salle 2", short_code=None),
			("Restaurant", "Bistro"): 3,
		}
		with (
			patch.object(frappe.db, "exists", return_value=True),
			patch.object(frappe.db, "has_column", return_value=True),
			patch.object(frappe.db, "get_value", self.lookups(found)),
		):
			self.assertEqual(room_and_digits_of(doc), ("S02", 3))

	def test_the_room_of_the_table_is_handed_to_pos_next_to_be_coded_and_its_answer_is_kept(self):
		doc = order()
		doc.restaurant_table = TABLE_NAME
		found = {
			("Restaurant Table", TABLE_NAME): frappe._dict(area="AREA-1", restaurant="Bistro"),
			("Restaurant Area", "AREA-1"): frappe._dict(area_name="Salle 2", short_code=None),
			("Restaurant", "Bistro"): 3,
		}
		module, installed = a_pos_next_that_codes_every_room_as("ZZ9")
		with (
			installed,
			patch.object(frappe.db, "exists", return_value=True),
			patch.object(frappe.db, "has_column", return_value=True),
			patch.object(frappe.db, "get_value", self.lookups(found)),
		):
			self.assertEqual(room_and_digits_of(doc), ("ZZ9", 3))
		# what the room says it is called, and nothing typed on it, is what POS Next is asked to make a code of
		module.room_code.assert_called_once_with("", "Salle 2")

	def test_a_code_typed_on_the_room_wins(self):
		doc = order()
		doc.restaurant_table = TABLE_NAME
		found = {
			("Restaurant Table", TABLE_NAME): frappe._dict(area="AREA-1", restaurant="Bistro"),
			("Restaurant Area", "AREA-1"): frappe._dict(area_name="Salle 2", short_code="RDC"),
			("Restaurant", "Bistro"): None,
		}
		with (
			patch.object(frappe.db, "exists", return_value=True),
			patch.object(frappe.db, "has_column", return_value=True),
			patch.object(frappe.db, "get_value", self.lookups(found)),
		):
			self.assertEqual(room_and_digits_of(doc), ("RDC", 4))

	def test_a_takeaway_order_has_the_code_of_a_takeaway(self):
		doc = order()
		doc.restaurant_table = None
		doc.is_takeaway = 1
		with (
			patch.object(frappe.db, "exists", return_value=True),
			patch.object(frappe.db, "has_column", return_value=True),
			patch.object(frappe.db, "get_value", return_value=None),
		):
			self.assertEqual(room_and_digits_of(doc), ("EMP", 4))

	def test_a_takeaway_order_has_the_digits_of_the_restaurant_of_the_till_that_took_it(self):
		doc = order()
		doc.restaurant_table = None
		doc.is_takeaway = 1
		doc.pos_profile = "Till"
		found = {
			("POS Settings", (("enabled", 1), ("pos_profile", "Till"))): "Bistro",
			("Restaurant", "Bistro"): 3,
		}
		with (
			patch.object(frappe.db, "exists", return_value=True),
			patch.object(frappe.db, "has_column", return_value=True),
			patch.object(frappe.db, "get_value", self.lookups(found)),
		):
			self.assertEqual(room_and_digits_of(doc), ("EMP", 3))

	def test_without_pos_next_there_is_no_room_and_four_digits(self):
		doc = order()
		doc.restaurant_table = TABLE_NAME
		with patch.object(frappe.db, "exists", return_value=False), patch.object(frappe.db, "get_value") as found:
			self.assertEqual(room_and_digits_of(doc), ("", 4))
		found.assert_not_called()
