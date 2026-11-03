"""The size of the writing of a kitchen ticket (NeoResto lot 1, Daniel, 03.11.2026).

« J'aimerais avoir peut-être les écritures un petit peu plus petites. » Each preparation station sets the size of its tickets (POS Next field
`ticket_text_size`: Large, Medium, Small). Large is what the tickets always were; Medium, the default, is a little smaller: the dishes keep their
height and lose half of their width, so that « Salade de chèvre chaud » stays on one line on the paper of the office printer.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from cloudprnt.kitchen_ticket import (
	DEFAULT_TEXT_SIZE,
	LINE_CHARS,
	TEXT_SIZES,
	_lines,
	get_kitchen_ticket_markup,
	station_text_size,
)
from cloudprnt.tests.test_kitchen_ticket import line, order
from cloudprnt.till import _write_text_size


def markup_at(size):
	doc = order()
	with patch("cloudprnt.kitchen_ticket.station_text_size", return_value=TEXT_SIZES[size]):
		return get_kitchen_ticket_markup(doc, "Kitchen", _lines(doc, "Kitchen", None))


class TestTheSizeOfAKitchenTicket(FrappeTestCase):
	def test_large_is_what_the_tickets_always_were(self):
		markup = markup_at("Large")

		self.assertIn("[magnify: width 2; height 2][bold: on]1 x Tiramisu[bold: off][magnify]", markup)
		self.assertIn("\n   [magnify: width 1; height 2]> For the birthday[magnify]", markup)
		self.assertIn("[magnify: width 3; height 3][bold: on]Table 5[bold: off][magnify]", markup)

	def test_medium_keeps_the_height_of_a_dish_and_halves_its_width(self):
		markup = markup_at("Medium")

		self.assertIn("[magnify: width 1; height 2][bold: on]1 x Tiramisu[bold: off][magnify]", markup)
		self.assertIn("[magnify: width 2; height 2][bold: on]Table 5[bold: off][magnify]", markup)
		self.assertIn("\n   > For the birthday\n", markup, "an option is written at the printer's own size")

	def test_small_writes_a_dish_at_the_printers_own_size_in_bold(self):
		markup = markup_at("Small")

		self.assertIn("\n[bold: on]1 x Tiramisu[bold: off]\n", markup)
		self.assertNotIn("height 2", markup)

	def test_every_size_is_smaller_than_the_one_before(self):
		area = lambda size: TEXT_SIZES[size]["dish"][0] * TEXT_SIZES[size]["dish"][1]  # noqa: E731

		self.assertGreater(area("Large"), area("Medium"))
		self.assertGreater(area("Medium"), area("Small"))

	def test_the_default_is_medium_a_little_smaller_than_before(self):
		self.assertEqual(DEFAULT_TEXT_SIZE, "Medium")

	def test_a_long_dish_name_fits_one_line_of_the_office_printer_in_medium_not_in_large(self):
		name = "-1 x Salade de chèvre chaud"

		self.assertGreater(len(name) * TEXT_SIZES["Large"]["dish"][0], LINE_CHARS[80])
		self.assertLessEqual(len(name) * TEXT_SIZES["Medium"]["dish"][0], LINE_CHARS[80])

	def test_a_cancellation_is_written_at_the_size_of_its_station(self):
		doc = order()
		lines = [line(item_name="Tiramisu", qty=1)]
		with patch("cloudprnt.kitchen_ticket.station_text_size", return_value=TEXT_SIZES["Small"]):
			markup = get_kitchen_ticket_markup(doc, "Kitchen", lines, title="CANCELLED 19:04", cancelled=True)

		self.assertIn("\n[bold: on]-1 x Tiramisu[bold: off]\n", markup)
		self.assertIn("[magnify: width 2; height 1][bold: on]CANCELLED 19:04[bold: off][magnify]", markup)


class TestTheSizeAStationSets(FrappeTestCase):
	def size_of(self, station, stored, column=True):
		with (
			patch.object(frappe.db, "exists", return_value=True),
			patch.object(frappe.db, "has_column", return_value=column),
			patch.object(frappe.db, "get_value", return_value=stored),
		):
			return station_text_size(station)

	def test_a_station_gets_the_size_it_sets(self):
		self.assertEqual(self.size_of("Kitchen", "Small"), TEXT_SIZES["Small"])
		self.assertEqual(self.size_of("Kitchen", "Large"), TEXT_SIZES["Large"])

	def test_a_station_that_sets_nothing_gets_the_default(self):
		self.assertEqual(self.size_of("Kitchen", None), TEXT_SIZES[DEFAULT_TEXT_SIZE])
		self.assertEqual(self.size_of("Kitchen", ""), TEXT_SIZES[DEFAULT_TEXT_SIZE])

	def test_a_size_nobody_knows_gets_the_default(self):
		self.assertEqual(self.size_of("Kitchen", "Huge"), TEXT_SIZES[DEFAULT_TEXT_SIZE])

	def test_a_pos_next_without_the_field_gets_the_default(self):
		self.assertEqual(self.size_of("Kitchen", "Small", column=False), TEXT_SIZES[DEFAULT_TEXT_SIZE])

	def test_a_ticket_of_no_station_gets_the_default(self):
		self.assertEqual(self.size_of(None, "Small"), TEXT_SIZES[DEFAULT_TEXT_SIZE])


class TestTheSizeTheTillWrites(FrappeTestCase):
	def written(self, sizes, column=True):
		with (
			patch.object(frappe.db, "exists", return_value=True),
			patch.object(frappe.db, "has_column", return_value=column),
			patch.object(frappe.db, "set_value") as set_value,
		):
			_write_text_size(sizes)
		return [call.args for call in set_value.call_args_list]

	def test_the_size_chosen_is_written_on_the_station(self):
		self.assertEqual(
			self.written({"Kitchen": "Small"}), [("Preparation Station", "Kitchen", "ticket_text_size", "Small")]
		)

	def test_a_size_nobody_knows_is_not_written(self):
		self.assertEqual(self.written({"Kitchen": "Huge", "Bar": ""}), [])

	def test_nothing_is_written_when_pos_next_has_not_the_field(self):
		self.assertEqual(self.written({"Kitchen": "Small"}, column=False), [])
