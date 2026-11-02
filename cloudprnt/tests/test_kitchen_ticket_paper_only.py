"""A kitchen that works on paper alone: ONE ticket for the whole order, every family on it (NeoResto lot 1, Daniel, 02.11.2026).

« Quand tu imprimes un ticket où tu as toutes les familles dessus, entrée, plat, dessert, c'est extrêmement simple: en cuisine ils tracent
physiquement sous le ticket, il n'y a rien à faire. » A station set « paper only » (POS Next field `paper_only` of the Preparation Station:
no screen, nothing followed on the screens or at the tables) prints what the waiter holds back too, in sections by family, so that the cooks
have the whole meal in their hands from the first send. What is fired later prints nothing: it is already on the paper (the kitchen counts
what it printed per dish). A station that is not paper only keeps its way: the lines at the kitchen only, a ticket per send. The suite
builds the order and the printers in memory and runs the real queue bookkeeping over a fake queue, as the cancellation's does.
"""

from cloudprnt.kitchen_ticket import on_kitchen_reprint
from cloudprnt.tests.test_kitchen_cancel import CancelCase
from cloudprnt.tests.test_kitchen_copy import an_order, line


def a_meal():
	"""One table's first send, as the till makes it: the first course leaves, the others are held back by the waiter."""
	return an_order(
		[
			line(item_name="Soup", service_family="Starter"),
			line(item_name="Steak", qty=2, service_family="Main", kds_status="Waiting", kds_batch=0),
			line(item_name="Tiramisu", service_family="Dessert", kds_status="Waiting", kds_batch=0),
			line(item_name="Wine", preparation_station="Bar", service_family="Drink"),
		]
	)


class PaperCase(CancelCase):
	def setUp(self):
		super().setUp()
		self.paper = {"Kitchen"}
		self.patch("cloudprnt.kitchen_ticket.paper_only_stations", lambda: self.paper)
		self.doc = a_meal()


class TestOneTicketForTheWholeMeal(PaperCase):
	def test_the_held_courses_are_on_the_first_ticket(self):
		kitchen = self.markups(self.send())["Kitchen printer"]

		for dish in ("1 x Soup", "2 x Steak", "1 x Tiramisu"):
			self.assertIn(dish, kitchen)

	def test_the_courses_come_in_sections_in_the_order_of_the_meal(self):
		kitchen = self.markups(self.send())["Kitchen printer"]

		self.assertLess(kitchen.index("STARTERS"), kitchen.index("Soup"))
		self.assertLess(kitchen.index("Soup"), kitchen.index("MAINS"))
		self.assertLess(kitchen.index("MAINS"), kitchen.index("Steak"))
		self.assertLess(kitchen.index("Steak"), kitchen.index("DESSERTS"))
		self.assertLess(kitchen.index("DESSERTS"), kitchen.index("Tiramisu"))

	def test_a_course_is_in_its_section_whatever_the_order_of_the_lines(self):
		self.doc.items = [self.doc.items[2], self.doc.items[0], self.doc.items[1], self.doc.items[3]]

		kitchen = self.markups(self.send())["Kitchen printer"]

		self.assertLess(kitchen.index("Soup"), kitchen.index("Steak"))
		self.assertLess(kitchen.index("Steak"), kitchen.index("Tiramisu"))

	def test_one_ticket_only_for_the_station(self):
		jobs = self.send()

		self.assertEqual(sorted(job.printer for job in jobs), ["Bar printer", "Kitchen printer"])

	def test_a_course_fired_later_prints_nothing_it_is_already_on_the_paper(self):
		self.send()
		steak = self.doc.items[1]
		steak.kds_status, steak.kds_batch = "Pending", 2

		self.assertEqual(self.send(batch=2), [])

	def test_a_dish_added_later_prints_alone_in_its_section(self):
		self.send()
		self.doc.items.append(line(item_name="Coffee", service_family="Dessert", kds_status="Pending", kds_batch=2))

		jobs = self.send(batch=2)

		self.assertEqual(len(jobs), 1)
		self.assertIn("1 x Coffee", jobs[0].markup)
		self.assertNotIn("Soup", jobs[0].markup, "what is on the paper already is not printed twice")

	def test_a_held_course_taken_off_comes_out_cancelled(self):
		self.send()
		self.doc.items = [item for item in self.doc.items if item.item_name != "Tiramisu"]

		jobs = self.send()

		self.assertEqual(len(jobs), 1)
		self.assertIn("CANCELLED", jobs[0].markup)
		self.assertIn("-1 x Tiramisu", jobs[0].markup)

	def test_a_line_the_waiter_has_not_sent_is_not_on_the_ticket(self):
		self.doc.items.append(line(item_name="Cheese", service_family="Dessert", kds_status="", kds_batch=0))

		kitchen = self.markups(self.send())["Kitchen printer"]

		self.assertNotIn("Cheese", kitchen)

	def test_a_guests_line_waiting_for_the_waiters_acceptance_is_not_on_the_ticket(self):
		# A QR order is held in a batch of its own until the waiter accepts it: it is not the waiter's held course
		self.doc.items.append(line(item_name="Guest burger", service_family="Main", kds_status="Waiting", kds_batch=3))

		kitchen = self.markups(self.send())["Kitchen printer"]

		self.assertNotIn("Guest burger", kitchen)

	def test_the_station_that_is_not_paper_only_prints_what_is_at_the_kitchen_only(self):
		self.paper = set()

		kitchen = self.markups(self.send())["Kitchen printer"]

		self.assertIn("1 x Soup", kitchen)
		self.assertNotIn("Steak", kitchen)
		self.assertNotIn("Tiramisu", kitchen)

	def test_a_station_with_one_family_has_no_section_title(self):
		self.doc.items = [line(item_name="Soup", service_family="Starter")]

		kitchen = self.markups(self.send())["Kitchen printer"]

		self.assertNotIn("STARTERS", kitchen)

	def test_a_line_with_no_family_is_listed_last_under_its_own_title(self):
		self.doc.items.append(line(item_name="Bread", service_family=""))

		kitchen = self.markups(self.send())["Kitchen printer"]

		self.assertLess(kitchen.index("Tiramisu"), kitchen.index("OTHER"))
		self.assertLess(kitchen.index("OTHER"), kitchen.index("Bread"))


class TestTheCopyOfAPaperOnlyTicket(PaperCase):
	def test_a_copy_holds_the_held_courses_too(self):
		self.send()

		answer = on_kitchen_reprint(self.doc.name, station="Kitchen")

		self.assertEqual(len(answer["tickets"]), 1)
		copy = self.queue.jobs[-1].markup
		for dish in ("Soup", "Steak", "Tiramisu"):
			self.assertIn(dish, copy)
		self.assertRegex(copy, r"COPY \d\d:\d\d")

	def test_a_copy_of_a_station_that_is_not_paper_only_leaves_the_held_courses_out(self):
		self.paper = set()
		self.send()

		on_kitchen_reprint(self.doc.name, station="Kitchen")

		copy = self.queue.jobs[-1].markup
		self.assertIn("Soup", copy)
		self.assertNotIn("Steak", copy)
