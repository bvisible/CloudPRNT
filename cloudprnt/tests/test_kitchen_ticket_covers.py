"""The number of people at a table on its kitchen ticket (NeoResto lot 1, product owner, 05.11.2026).

« Table 4, 3 personnes, puis 3 plats »: the ticket says how many people are seated under the table's name, and each course's heading says how
many plates the whole order holds of it against them (« MAINS 2/3 »), so that the cook sees nobody was forgotten. A table with no number prints
as it always did; a drink is no plate; a taken-back dish prints no count. The suite builds the order in memory, as the other ticket suites do."""

from frappe.tests.utils import FrappeTestCase

from cloudprnt.kitchen_ticket import get_kitchen_ticket_markup
from cloudprnt.tests.test_kitchen_copy import an_order, line


def meal(covers=None, **extra):
	doc = an_order(
		[
			line(item_name="Soup", qty=3, service_family="Starter"),
			line(item_name="Steak", qty=2, service_family="Main", kds_batch=2),
			line(item_name="Tiramisu", qty=1, service_family="Dessert", kds_batch=3),
			line(item_name="Water", qty=1, service_family="Drink"),
			line(item_name="Bar wine", qty=2, service_family="Drink", preparation_station="Bar"),
		]
	)
	if covers is not None:
		doc.restaurant_covers = covers
	for key, value in extra.items():
		setattr(doc, key, value)
	return doc


def kitchen_ticket(doc, lines=None, **options):
	return get_kitchen_ticket_markup(doc, "Kitchen", lines if lines is not None else doc.items[:4], **options)


class TestTheCoversOnTheTicket(FrappeTestCase):
	def test_the_number_of_people_is_under_the_table(self):
		markup = kitchen_ticket(meal(covers=3))

		self.assertIn("3 people", markup)
		self.assertLess(markup.index("Table 5"), markup.index("3 people"))
		self.assertLess(markup.index("3 people"), markup.index("Soup"))

	def test_each_course_heading_counts_the_plates_of_the_whole_order_against_the_people(self):
		markup = kitchen_ticket(meal(covers=3))

		self.assertIn("STARTERS 3/3", markup)
		self.assertIn("MAINS 2/3", markup)
		self.assertIn("DESSERTS 1/3", markup)

	def test_the_count_is_of_the_whole_order_not_of_the_lines_on_the_paper(self):
		# A ticket of the main course only, as a ticket per send or per family is: the order holds three soups and two steaks
		doc = meal(covers=3)
		markup = kitchen_ticket(doc, [doc.items[1]])

		self.assertIn("MAINS 2/3", markup)
		self.assertNotIn("STARTERS", markup)

	def test_a_drink_is_no_plate(self):
		markup = kitchen_ticket(meal(covers=3))

		self.assertIn("DRINKS", markup)
		self.assertNotRegex(markup, r"DRINKS \d")

	def test_a_table_without_a_number_prints_as_it_did(self):
		for covers in (None, 0, ""):
			with self.subTest(covers=covers):
				doc = meal()
				doc.restaurant_covers = covers
				markup = kitchen_ticket(doc)

				self.assertNotIn("people", markup)
				self.assertIn("STARTERS", markup)
				self.assertNotRegex(markup, r"STARTERS \d")

	def test_a_ticket_that_names_its_course_does_not_repeat_it_in_a_heading(self):
		# « 1/3 MAINS »: the title of a ticket per family says it, the count is not added as a second heading
		doc = meal(covers=3)
		markup = kitchen_ticket(doc, [doc.items[1]], title="2/3 MAINS")

		self.assertIn("2/3 MAINS", markup)
		self.assertNotIn("MAINS 2/3", markup)
		self.assertIn("3 people", markup)

	def test_a_dish_taken_back_prints_no_count(self):
		markup = kitchen_ticket(meal(covers=3), cancelled=True)

		self.assertNotIn("people", markup)
		self.assertNotRegex(markup, r"STARTERS \d")

	def test_a_quantity_is_read_as_it_is_ordered(self):
		doc = meal(covers=4)
		doc.items[1].qty = 1.5

		self.assertIn("MAINS 1.5/4", kitchen_ticket(doc))
