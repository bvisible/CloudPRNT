"""Which stations work on paper alone, and what the till's printing settings say about it (NeoResto lot 1, Daniel, 02.11.2026).

A paper-only station has no screen: it needs a printer, or nothing reaches the kitchen. The rule that keeps the two together lives in
`paper_flags`, which turns what the manager asked for and the printers as they will be into the flag each station ends with.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from cloudprnt.till import paper_flags


def printer(*stations):
	return frappe._dict(preparation_station=", ".join(stations))


class TestPaperFlags(FrappeTestCase):
	def test_a_station_with_a_printer_can_be_set_paper_only(self):
		self.assertEqual(paper_flags([printer("Kitchen")], {"Kitchen": 1}, set()), {"Kitchen": 1})

	def test_a_station_with_no_printer_cannot_be(self):
		self.assertEqual(paper_flags([printer("Bar")], {"Kitchen": 1}, set()), {"Kitchen": 0})

	def test_one_printer_may_serve_several_paper_only_stations(self):
		flags = paper_flags([printer("Bar", "Kitchen")], {"Kitchen": 1, "Bar": 1}, set())

		self.assertEqual(flags, {"Kitchen": 1, "Bar": 1})

	def test_a_station_can_be_given_back_its_screen(self):
		self.assertEqual(paper_flags([printer("Kitchen")], {"Kitchen": 0}, {"Kitchen"}), {"Kitchen": 0})

	def test_a_paper_only_station_that_loses_its_printer_is_not_paper_only_any_more(self):
		self.assertEqual(paper_flags([], {}, {"Kitchen"}), {"Kitchen": 0})

	def test_a_paper_only_station_that_keeps_its_printer_is_left_alone(self):
		self.assertEqual(paper_flags([printer("Kitchen")], {}, {"Kitchen"}), {})

	def test_the_flag_asked_for_as_text_counts(self):
		self.assertEqual(paper_flags([printer("Kitchen")], {"Kitchen": "1"}, set()), {"Kitchen": 1})
		self.assertEqual(paper_flags([printer("Kitchen")], {"Kitchen": "0"}, {"Kitchen"}), {"Kitchen": 0})
