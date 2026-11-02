"""The kitchen ticket of a dish that is taken back (NeoResto lot 1, Daniel, 02.11: no screen at all in the kitchen, only paper).

A kitchen that reads paper only never sees a line struck on a screen: a dish taken off a table's order, or a quantity lowered, after its
ticket came out, must come out on paper too, or it is cooked for nothing. The ticket is a CANCELLATION: the word big at the head of the
paper, the table, the quantities with a minus, no price. It is written down as a negative quantity, so that the next send counts what the
kitchen really holds: a dish put back later prints again. The suite builds the order and the printers in memory and runs the real queue
bookkeeping (`printed_quantities`) over a fake queue, as the copy's does.
"""

import json
from unittest.mock import patch

from cloudprnt.kitchen_ticket import on_kitchen_send, on_kitchen_void
from cloudprnt.tests.hostile_text import CLEAN, HOSTILE, unsafe_characters
from cloudprnt.tests.test_kitchen_copy import BAR_PRINTER, KITCHEN_PRINTER, KitchenCopyCase, an_order, line


class CancelCase(KitchenCopyCase):
	def setUp(self):
		super().setUp()
		# The tests name a dish by its code only: no Item to read its name from
		self.patch("cloudprnt.kitchen_ticket.frappe.db.get_value", lambda *args, **kwargs: None)

	def printed(self, invoice):
		printed = {}
		for rows in self.queue.printed(invoice):
			for key, qty in json.loads(rows).items():
				printed[key] = printed.get(key, 0) + qty
		return printed

	def send(self, batch=1):
		"""The send of the order, with the queue's own bookkeeping of what was printed; the jobs it made."""
		before = len(self.queue.jobs)
		with patch("cloudprnt.kitchen_ticket.printed_quantities", self.printed):
			on_kitchen_send(self.doc.name, batch)
		return self.queue.jobs[before:]

	def markups(self, jobs):
		return {job.printer: job.markup for job in jobs}


class TestADishTakenBack(CancelCase):
	def setUp(self):
		super().setUp()
		self.doc = an_order(
			[
				line(item_name="Steak", qty=2),
				line(item_name="Salad", qty=1),
				line(item_name="Wine", qty=1, preparation_station="Bar"),
			]
		)
		self.send()

	def test_a_dish_taken_off_the_order_comes_out_cancelled_at_the_station_that_cooks_it(self):
		self.doc.items = [item for item in self.doc.items if item.item_name != "Steak"]

		jobs = self.send()

		self.assertEqual(len(jobs), 1)
		self.assertEqual(jobs[0].printer, "Kitchen printer")
		markup = jobs[0].markup
		self.assertRegex(markup, r"CANCELLED \d\d:\d\d")
		self.assertLess(markup.index("CANCELLED"), markup.index("Table 5"), "the word is at the head of the paper")
		self.assertIn("-2 x Steak", markup)
		self.assertNotIn("Salad", markup, "what stays is not on the paper")
		self.assertNotIn("Wine", markup, "the bar's dish is the bar's")

	def test_a_quantity_lowered_cancels_the_difference(self):
		self.doc.items[0].qty = 1

		jobs = self.send()

		self.assertEqual(len(jobs), 1)
		self.assertIn("-1 x Steak", jobs[0].markup)

	def test_the_cancellation_is_written_down_so_that_it_prints_once(self):
		self.doc.items = [item for item in self.doc.items if item.item_name != "Steak"]
		self.send()

		self.assertEqual(self.send(), [], "the same order saved again prints nothing")
		self.assertEqual(self.printed(self.doc.name)["Kitchen|Steak|Nos||"], 0)

	def test_a_dish_put_back_after_its_cancellation_prints_again(self):
		steak = self.doc.items[0]
		self.doc.items = self.doc.items[1:]
		self.send()
		self.doc.items.insert(0, steak)

		jobs = self.send()

		self.assertEqual(len(jobs), 1)
		self.assertNotIn("CANCELLED", jobs[0].markup)
		self.assertIn("2 x Steak", jobs[0].markup)

	def test_the_last_dishes_of_the_order_taken_off_still_come_out_cancelled(self):
		self.doc.items = []

		jobs = self.send()

		self.assertEqual(set(self.markups(jobs)), {"Kitchen printer", "Bar printer"})
		for markup in self.markups(jobs).values():
			self.assertIn("CANCELLED", markup)

	def test_each_station_cancels_its_own_dishes_on_its_own_printer(self):
		self.doc.items = [item for item in self.doc.items if item.item_name == "Salad"]

		by_printer = self.markups(self.send())

		self.assertIn("-2 x Steak", by_printer["Kitchen printer"])
		self.assertNotIn("Wine", by_printer["Kitchen printer"])
		self.assertIn("-1 x Wine", by_printer["Bar printer"])

	def test_the_remarks_of_the_dish_are_on_the_cancellation(self):
		self.doc.items[0].posa_special_instructions = "no salt"
		self.send()
		self.doc.items = self.doc.items[1:]

		jobs = self.send()

		self.assertIn("-2 x Steak", jobs[-1].markup)
		self.assertIn("> no salt", jobs[-1].markup)

	def test_the_cancellation_carries_no_price_and_the_ticket_number(self):
		self.doc.posa_ticket_no = 7
		self.doc.items[0].rate = 38.5
		self.doc.items = self.doc.items[1:]

		markup = self.send()[0].markup

		self.assertIn("Table 5-0007", markup)
		self.assertNotIn("38.5", markup)


class TestWhatIsNotACancellation(CancelCase):
	def test_a_dish_that_never_came_out_on_paper_is_not_cancelled(self):
		# The kitchen had no printer when the dish was sent: nothing came out, so there is nothing to take back
		self.doc = an_order([line(item_name="Steak", qty=2), line(item_name="Wine", preparation_station="Bar")])
		self.printers = [BAR_PRINTER]
		self.send()
		self.printers = [KITCHEN_PRINTER, BAR_PRINTER]
		self.doc.items = [item for item in self.doc.items if item.preparation_station != "Kitchen"]

		jobs = self.send()

		self.assertEqual([job for job in jobs if job.printer == "Kitchen printer"], [])

	def test_a_dish_whose_status_is_lost_in_a_save_is_not_taken_back(self):
		self.doc = an_order([line(item_name="Steak", qty=2)])
		self.send()
		# A save that did not carry the line's status: the line is still on the order
		self.doc.items[0].kds_status = ""

		self.assertEqual(self.send(), [])

	def test_a_course_held_back_that_goes_off_the_order_prints_nothing(self):
		self.doc = an_order([line(item_name="Tiramisu", kds_status="Waiting")])
		self.send()
		self.doc.items = []

		self.assertEqual(self.send(), [])

	def test_a_dish_added_prints_as_it_always_did(self):
		self.doc = an_order([line(item_name="Steak", qty=2)])
		self.send()
		self.doc.items.append(line(item_name="Burger", qty=3))

		jobs = self.send()

		self.assertEqual(len(jobs), 1)
		self.assertIn("3 x Burger", jobs[0].markup)
		self.assertNotIn("CANCELLED", jobs[0].markup)

	def test_a_cancellation_and_a_new_dish_in_one_save_make_two_pieces_of_paper(self):
		self.doc = an_order([line(item_name="Steak", qty=2)])
		self.send()
		self.doc.items = [line(item_name="Burger", qty=1)]

		jobs = self.send()

		self.assertEqual(len(jobs), 2)
		cancelled = [job for job in jobs if "CANCELLED" in job.markup]
		new = [job for job in jobs if "CANCELLED" not in job.markup]
		self.assertIn("-2 x Steak", cancelled[0].markup)
		self.assertIn("1 x Burger", new[0].markup)


class TestTheCancellationIsSafePaper(CancelCase):
	def test_no_control_character_reaches_the_printer_whatever_a_remark_or_a_name_says(self):
		self.doc = an_order([line(item_name=HOSTILE, qty=1, posa_special_instructions=HOSTILE)])
		self.send()
		self.doc.items = []

		markup = self.send()[0].markup

		self.assertEqual(unsafe_characters(markup), [])
		self.assertIn(f"-1 x {CLEAN}", markup)


class TestAnOrderThatIsGone(CancelCase):
	"""A table's draft order deleted after it went to the kitchen: every dish that came out on paper comes out cancelled."""

	def void(self):
		before = len(self.queue.jobs)
		with patch("cloudprnt.kitchen_ticket.printed_quantities", self.printed):
			on_kitchen_void(self.doc.name)
		return self.queue.jobs[before:]

	def setUp(self):
		super().setUp()
		self.doc = an_order(
			[
				line(item_name="Steak", qty=2),
				line(item_name="Wine", qty=1, preparation_station="Bar"),
				line(item_name="Tiramisu", preparation_station="Pastry", kds_status="Waiting"),
			]
		)
		self.send()

	def test_every_dish_that_came_out_comes_out_cancelled_on_its_own_printer(self):
		by_printer = self.markups(self.void())

		self.assertEqual(set(by_printer), {"Kitchen printer", "Bar printer"})
		self.assertIn("-2 x Steak", by_printer["Kitchen printer"])
		self.assertIn("-1 x Wine", by_printer["Bar printer"])
		self.assertRegex(by_printer["Kitchen printer"], r"CANCELLED \d\d:\d\d")

	def test_a_course_that_was_held_back_never_came_out_and_is_not_cancelled(self):
		for markup in self.markups(self.void()).values():
			self.assertNotIn("Tiramisu", markup)

	def test_it_is_told_once(self):
		self.void()

		self.assertEqual(self.void(), [])

	def test_an_order_that_printed_nothing_is_gone_without_a_word(self):
		self.queue.jobs.clear()

		self.assertEqual(self.void(), [])
