"""The kitchen ticket of an ADDITION (NeoResto lot 1; product owner, 05.11.2026).

« Il faut faire un pop-up, parce qu'autrement, en cuisine, ils ne savent pas qu'on a rajouté cette information sur leur ticket, et il faut imprimer
le ticket avec l'ajout. » A dish added to a table the kitchen already has comes out on its own ticket (the send's), headed « *** ADDITION *** » in
the largest type, so that it is not read as a second copy of the table's ticket. POS Next says which dishes a save added, in the request's flags
(`frappe.flags.kitchen_additions`, `{item_code: units}`); a send that carries none, the next course the waiter orders included, prints as it always did.
The suite builds the order and the printers in memory and runs the real queue bookkeeping over a fake queue, as the cancellations' does.
"""

import json
from unittest.mock import patch

import frappe

from cloudprnt.kitchen_ticket import get_kitchen_ticket_markup, on_kitchen_send
from cloudprnt.tests.test_kitchen_copy import KitchenCopyCase, an_order, line


class TestAnAdditionIsHeaded(KitchenCopyCase):
	def setUp(self):
		super().setUp()
		# The tests name a dish by its code only: no Item to read its name from
		self.patch("cloudprnt.kitchen_ticket.frappe.db.get_value", lambda *args, **kwargs: None)
		self.addCleanup(setattr, frappe.flags, "kitchen_additions", None)
		# POS Next stores the number of a send counting from zero: the first send carries none (0), the second 1, the third 2
		self.doc = an_order(
			[
				line(item_name="Steak", qty=2, kds_batch=0),
				line(item_name="Salad", qty=1, kds_batch=0),
				line(item_name="Wine", qty=1, preparation_station="Bar", kds_batch=0),
			]
		)
		self.send(0)

	def printed(self, invoice):
		printed = {}
		for rows in self.queue.printed(invoice):
			for key, qty in json.loads(rows).items():
				printed[key] = printed.get(key, 0) + qty
		return printed

	def send(self, batch):
		before = len(self.queue.jobs)
		with patch("cloudprnt.kitchen_ticket.printed_quantities", self.printed):
			on_kitchen_send(self.doc.name, batch)
		return self.queue.jobs[before:]

	def test_a_dish_added_after_the_first_send_comes_out_headed_addition_above_the_table(self):
		self.doc.items.append(line(item_name="Tartare", qty=1, kds_batch=1))
		frappe.flags.kitchen_additions = {"Tartare": 1}

		jobs = self.send(1)

		self.assertEqual(len(jobs), 1)
		markup = jobs[0].markup
		self.assertIn("*** ADDITION ***", markup)
		self.assertLess(markup.index("ADDITION"), markup.index("Table 5"), "the word is at the head of the paper")
		self.assertIn("1 x Tartare", markup)
		self.assertNotIn("Steak", markup, "what the kitchen already had is not printed again")
		self.assertIn("Send no. 2", markup, "the second send of the table: what the kitchen's screen says too (« Send 2 »)")

	def test_the_send_is_numbered_as_the_kitchens_screen_numbers_it(self):
		# The first send says no number, the second « Send no. 2 », the third « Send no. 3 »: POS Next stores them 0, 1, 2
		first = self.queue.jobs[0].markup
		self.assertNotIn("Send no.", first)
		self.doc.items.append(line(item_name="Tartare", qty=1, kds_batch=1))
		second = self.send(1)[0].markup
		self.doc.items.append(line(item_name="Cheese", qty=1, kds_batch=2))
		third = self.send(2)[0].markup

		self.assertIn("Send no. 2", second)
		self.assertNotIn("Send no. 1", second, "the second send was told « Send no. 1 » on paper while the screen said 2")
		self.assertIn("Send no. 3", third)

	def test_a_send_that_added_nothing_prints_as_it_always_did(self):
		self.doc.items.append(line(item_name="Tartare", qty=1, kds_batch=1))

		jobs = self.send(1)

		self.assertEqual(len(jobs), 1)
		self.assertNotIn("ADDITION", jobs[0].markup)
		self.assertIn("1 x Tartare", jobs[0].markup)

	def test_the_next_course_the_waiter_orders_is_not_an_addition(self):
		# A dessert held back at the first send is released: more for the kitchen, nothing added to the order, and POS Next says so
		self.doc.items.append(line(item_name="Tiramisu", qty=1, preparation_station="Kitchen", kds_status="Pending", kds_batch=1))
		frappe.flags.kitchen_additions = {}

		jobs = self.send(1)

		self.assertEqual(len(jobs), 1)
		self.assertNotIn("ADDITION", jobs[0].markup)

	def test_only_the_station_that_got_the_added_dish_heads_its_ticket(self):
		self.doc.items.append(line(item_name="Beer", qty=1, preparation_station="Bar", kds_batch=1))
		frappe.flags.kitchen_additions = {"Beer": 1}

		jobs = self.send(1)

		self.assertEqual(len(jobs), 1)
		self.assertEqual(jobs[0].printer, "Bar printer")
		self.assertIn("ADDITION", jobs[0].markup)

	def test_a_cancellation_is_never_headed_addition(self):
		doc = an_order([line(item_name="Steak", qty=2)])

		markup = get_kitchen_ticket_markup(doc, "Kitchen", doc.items, 2, cancelled=True, addition=True)

		self.assertNotIn("ADDITION", markup)

	def test_a_ticket_not_asked_to_be_an_addition_is_not_one(self):
		doc = an_order([line(item_name="Steak", qty=2)])

		self.assertNotIn("ADDITION", get_kitchen_ticket_markup(doc, "Kitchen", doc.items, 2))
		self.assertIn("*** ADDITION ***", get_kitchen_ticket_markup(doc, "Kitchen", doc.items, 2, addition=True))
