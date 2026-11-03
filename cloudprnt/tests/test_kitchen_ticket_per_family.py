"""One ticket per family at the kitchen (NeoResto lot 1, Daniel, 03.11.2026; decided on 28.10, design note NeoResto/02 § E).

« Quand ça part en cuisine, c'est entrée, plat, dessert : un seul ticket, ou trois tickets. Ça permettrait de donner le ticket avec le plat au
moment où il sort, puis les deux autres tickets suivent. » A preparation station set « a ticket per family » (POS Next field `ticket_per_family`,
off by default) prints, when a send holds several families, one ticket for each, in the order of the meal, numbered « 1/3 STARTERS ». Where the
station keeps the whole meal on paper (paper only), the families the waiter holds back are tickets too: « WAIT FOR THE NEXT COURSE ». Each ticket
writes down its own dishes, so that nothing is printed twice. A station without the setting keeps its way: one ticket, in sections. The suite
builds the order and the printers in memory and runs the real queue bookkeeping over a fake queue, as the cancellation's does.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from cloudprnt.kitchen_ticket import ticket_per_family_stations
from cloudprnt.tests.test_kitchen_cancel import CancelCase
from cloudprnt.tests.test_kitchen_copy import an_order, line
from cloudprnt.tests.test_kitchen_ticket_paper_only import a_meal
from cloudprnt.till import _write_ticket_per_family


class FamilyCase(CancelCase):
	def setUp(self):
		super().setUp()
		self.paper = {"Kitchen"}
		self.per_family = {"Kitchen"}
		self.patch("cloudprnt.kitchen_ticket.paper_only_stations", lambda: self.paper)
		self.patch("cloudprnt.kitchen_ticket.ticket_per_family_stations", lambda: self.per_family)
		self.doc = a_meal()

	def kitchen(self, jobs):
		return [job for job in jobs if job.printer == "Kitchen printer"]


class TestATicketPerFamily(FamilyCase):
	def test_each_family_of_the_meal_comes_out_on_its_own_ticket_in_the_order_of_the_meal(self):
		tickets = self.kitchen(self.send())

		self.assertEqual(len(tickets), 3)
		self.assertIn("1/3 STARTERS", tickets[0].markup)
		self.assertIn("2/3 MAINS", tickets[1].markup)
		self.assertIn("3/3 DESSERTS", tickets[2].markup)

	def test_a_ticket_holds_the_dishes_of_its_family_only(self):
		starters, mains, desserts = [job.markup for job in self.kitchen(self.send())]

		self.assertIn("1 x Soup", starters)
		self.assertNotIn("Steak", starters)
		self.assertIn("2 x Steak", mains)
		self.assertNotIn("Soup", mains)
		self.assertIn("1 x Tiramisu", desserts)
		self.assertNotIn("Steak", desserts)

	def test_the_title_stands_at_the_head_of_the_paper_before_the_table(self):
		mains = self.kitchen(self.send())[1].markup

		self.assertLess(mains.index("2/3 MAINS"), mains.index("Table 5"))

	def test_the_family_the_waiter_holds_back_says_to_wait_and_the_one_that_leaves_says_to_do_it(self):
		starters, mains, desserts = [job.markup for job in self.kitchen(self.send())]

		self.assertIn("TO DO", starters)
		self.assertNotIn("WAIT FOR THE NEXT COURSE", starters)
		self.assertIn("WAIT FOR THE NEXT COURSE", mains)
		self.assertIn("WAIT FOR THE NEXT COURSE", desserts)

	def test_each_ticket_writes_down_its_own_dishes_so_that_nothing_is_printed_twice(self):
		self.send()

		printed = self.printed(self.doc.name)
		self.assertEqual(printed["Kitchen|Soup|Nos||"], 1)
		self.assertEqual(printed["Kitchen|Steak|Nos||"], 2)
		self.assertEqual(printed["Kitchen|Tiramisu|Nos||"], 1)
		self.assertEqual(self.send(), [], "the same order saved again prints nothing")

	def test_a_course_fired_later_prints_nothing_it_is_already_on_the_paper(self):
		self.send()
		steak = self.doc.items[1]
		steak.kds_status, steak.kds_batch = "Pending", 2

		self.assertEqual(self.send(batch=2), [])

	def test_a_dish_added_later_prints_alone_without_a_number(self):
		self.send()
		self.doc.items.append(line(item_name="Coffee", service_family="Dessert", kds_status="Pending", kds_batch=2))

		jobs = self.send(batch=2)

		self.assertEqual(len(jobs), 1)
		self.assertIn("1 x Coffee", jobs[0].markup)
		self.assertNotIn("1/1", jobs[0].markup, "one family is one ticket: nothing to count")

	def test_the_other_station_keeps_one_ticket(self):
		jobs = self.send()

		bar = [job for job in jobs if job.printer == "Bar printer"]
		self.assertEqual(len(bar), 1)
		self.assertIn("1 x Wine", bar[0].markup)
		self.assertNotIn("/", bar[0].markup.split("Table 5")[0], "no numbered title at the bar")

	def test_a_dish_taken_back_still_comes_out_as_one_cancellation(self):
		self.send()
		self.doc.items = [item for item in self.doc.items if item.item_name != "Tiramisu"]

		jobs = self.send()

		self.assertEqual(len(jobs), 1)
		self.assertIn("CANCELLED", jobs[0].markup)
		self.assertIn("-1 x Tiramisu", jobs[0].markup)


class TestAStationWithoutTheSetting(FamilyCase):
	def test_it_prints_one_ticket_with_the_families_in_sections(self):
		self.per_family = set()

		tickets = self.kitchen(self.send())

		self.assertEqual(len(tickets), 1)
		for heading in ("STARTERS", "MAINS", "DESSERTS"):
			self.assertIn(heading, tickets[0].markup)
		self.assertNotIn("1/3", tickets[0].markup)
		self.assertNotIn("WAIT FOR THE NEXT COURSE", tickets[0].markup)


class TestAStationThatHoldsNothingBack(FamilyCase):
	def setUp(self):
		super().setUp()
		self.paper = set()

	def test_two_families_sent_together_are_two_numbered_tickets_with_no_waiting_note(self):
		self.doc = an_order(
			[
				line(item_name="Soup", service_family="Starter"),
				line(item_name="Steak", service_family="Main"),
			]
		)

		tickets = self.kitchen(self.send())

		self.assertEqual(len(tickets), 2)
		self.assertIn("1/2 STARTERS", tickets[0].markup)
		self.assertIn("2/2 MAINS", tickets[1].markup)
		for ticket in tickets:
			self.assertNotIn("TO DO", ticket.markup)
			self.assertNotIn("WAIT FOR THE NEXT COURSE", ticket.markup)

	def test_one_family_alone_is_the_usual_ticket_without_a_number(self):
		self.doc = an_order([line(item_name="Soup", service_family="Starter")])

		tickets = self.kitchen(self.send())

		self.assertEqual(len(tickets), 1)
		self.assertNotIn("1/1", tickets[0].markup)
		self.assertNotIn("STARTERS", tickets[0].markup)


class TestTheStationsThatPrintATicketPerFamily(FrappeTestCase):
	def stations(self, column=True, names=("Kitchen",)):
		with (
			patch.object(frappe.db, "exists", return_value=True),
			patch.object(frappe.db, "has_column", return_value=column),
			patch.object(frappe, "get_all", return_value=list(names)) as get_all,
		):
			found = ticket_per_family_stations()
		return found, get_all

	def test_the_stations_that_ask_for_it_are_the_ones_the_field_flags(self):
		found, get_all = self.stations(names=("Kitchen", "Pizza"))

		self.assertEqual(found, {"Kitchen", "Pizza"})
		self.assertEqual(get_all.call_args.kwargs["filters"], {"ticket_per_family": 1})

	def test_a_pos_next_without_the_field_has_none(self):
		found, get_all = self.stations(column=False)

		self.assertEqual(found, set())
		get_all.assert_not_called()


class TestTheFlagTheTillWrites(FrappeTestCase):
	def written(self, flags, column=True):
		with (
			patch.object(frappe.db, "exists", return_value=True),
			patch.object(frappe.db, "has_column", return_value=column),
			patch.object(frappe.db, "set_value") as set_value,
		):
			_write_ticket_per_family(flags)
		return [call.args for call in set_value.call_args_list]

	def test_the_choice_is_written_on_the_station(self):
		self.assertEqual(
			self.written({"Kitchen": 1, "Bar": 0}),
			[
				("Preparation Station", "Kitchen", "ticket_per_family", 1),
				("Preparation Station", "Bar", "ticket_per_family", 0),
			],
		)

	def test_the_choice_asked_for_as_text_counts(self):
		self.assertEqual(self.written({"Kitchen": "1"}), [("Preparation Station", "Kitchen", "ticket_per_family", 1)])
		self.assertEqual(self.written({"Kitchen": "0"}), [("Preparation Station", "Kitchen", "ticket_per_family", 0)])

	def test_nothing_is_written_when_pos_next_has_not_the_field(self):
		self.assertEqual(self.written({"Kitchen": 1}, column=False), [])
