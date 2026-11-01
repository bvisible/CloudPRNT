"""The copy of a kitchen ticket, printed from the kitchen's or the runner's screen (NeoRestau lot 1, task 44).

A big order is carried to the pass on paper: the screen's printer button asks POS Next, which names the moment through the
hook `pos_next_kitchen_reprint`; this app prints. The copy is the lines of ONE station that are at the kitchen (or the whole
order), marked COPY and the time so that nobody cooks twice. A copy is not a send: it never marks a line as printed, so the
next send prints what it always would have. The suite builds the order and the printers in memory and runs the real queue
bookkeeping of the normal send (`printed_quantities`) over a fake queue.
"""

import json
import time
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from cloudprnt import kitchen_ticket
from cloudprnt import print_queue_manager as jobs
from cloudprnt.kitchen_ticket import (
	kitchen_copy_status,
	on_kitchen_reprint,
	on_kitchen_send,
	stations_with_a_printer,
)


class Order(SimpleNamespace):
	def get(self, key, default=None):
		return getattr(self, key, default)


def line(**values):
	# A dish is what the normal send counts by (item, unit, options, remarks): each line a dish of its own
	values.setdefault("item_code", values.get("item_name", "x"))
	return frappe._dict(
		{
			"item_name": "x",
			"qty": 1,
			"uom": "Nos",
			"preparation_station": "Kitchen",
			"kds_status": "Pending",
			"kds_batch": 1,
			**values,
		}
	)


def an_order(items=None):
	return Order(
		name=f"ACC-SINV-TEST-{uuid.uuid4().hex[:8]}",
		owner="Administrator",
		restaurant_table="Table 5",
		customer_name="Walk-in",
		items=items
		if items is not None
		else [
			line(item_name="Steak", qty=2),
			line(item_name="Salad", qty=1, kds_batch=2),
			line(item_name="Wine", preparation_station="Bar"),
			line(item_name="Tiramisu", preparation_station="Pastry", kds_status="Waiting"),
			line(item_name="Soup", kds_status="Delivered"),
		],
	)


def printer(label, stations, online=1, width=80):
	return frappe._dict(
		name=label, label=label, preparation_station=", ".join(stations), online=online, paper_width=width
	)


KITCHEN_PRINTER = printer("Kitchen printer", ["Kitchen"])
BAR_PRINTER = printer("Bar printer", ["Bar"])


class FakeQueue:
	"""What `jobs.enqueue` does for a ticket, kept in memory: the jobs, and the kitchen rows each one remembers."""

	def __init__(self):
		self.jobs = []

	def enqueue(self, row, markup, **values):
		token = f"tok-{len(self.jobs) + 1}"
		self.jobs.append(frappe._dict(printer=row.label, markup=markup, token=token, **values))
		return {"success": True, "job_token": token, "queue_position": len(self.jobs)}

	def printed(self, invoice):
		"""What the queue's rows say the kitchen printed: the same sum as `printed_quantities`."""
		rows = [
			json.dumps(j.kitchen_rows)
			for j in self.jobs
			if j.get("kitchen_rows") and j.get("reference_name") == invoice
		]
		return rows


class KitchenCopyCase(FrappeTestCase):
	def setUp(self):
		super().setUp()
		self.queue = FakeQueue()
		self.printers = [KITCHEN_PRINTER, BAR_PRINTER]
		self.doc = an_order()
		self.patch("cloudprnt.kitchen_ticket.jobs.enqueue", self.queue.enqueue)
		self.patch("cloudprnt.kitchen_ticket.all_printers", lambda: self.printers)
		self.patch("cloudprnt.kitchen_ticket.frappe.get_doc", lambda *args, **kwargs: self.doc)
		self.patch("cloudprnt.labels.label_of", lambda doctype, field, name: name)
		self.patch("cloudprnt.labels.room_and_digits_of", lambda doc: ("", 4))
		self.addCleanup(self.forget_claims)

	def patch(self, target, replacement):
		patcher = patch(target, replacement)
		patcher.start()
		self.addCleanup(patcher.stop)

	def forget_claims(self):
		frappe.cache().delete_keys("cloudprnt_kitchen_copy:")

	def copy(self, station="Kitchen", **values):
		return on_kitchen_reprint(self.doc.name, station=station, **values)


class TestWhatACopyPrints(KitchenCopyCase):
	def test_a_copy_is_marked_with_the_word_and_the_time_so_that_nobody_cooks_twice(self):
		answer = self.copy()

		self.assertEqual(len(self.queue.jobs), 1)
		markup = self.queue.jobs[0].markup
		self.assertRegex(markup, r"COPY \d\d:\d\d")
		# the word stands before the table, big, at the head of the paper
		self.assertLess(markup.index("COPY "), markup.index("Table 5"))
		self.assertEqual([t["station"] for t in answer["tickets"]], ["Kitchen"])

	def test_the_mark_can_be_switched_off(self):
		self.copy(mark_copy=False)

		self.assertNotIn("COPY", self.queue.jobs[0].markup)
		self.assertIn("Steak", self.queue.jobs[0].markup)

	def test_a_copy_holds_the_lines_of_the_station_that_are_at_the_kitchen_and_no_other(self):
		self.copy()

		markup = self.queue.jobs[0].markup
		self.assertIn("2 x Steak", markup)
		self.assertIn("1 x Salad", markup, "a line of another send is on the same ticket of the screen")
		self.assertNotIn("Wine", markup, "the bar's line is the bar's")
		self.assertNotIn("Tiramisu", markup, "a course held back is not at the kitchen yet")
		self.assertNotIn("Soup", markup, "a delivered dish is done with")

	def test_a_copy_for_the_whole_order_holds_every_line_at_the_kitchen_on_the_printer_of_the_station(self):
		self.copy(whole_order=True)

		self.assertEqual(len(self.queue.jobs), 1)
		job = self.queue.jobs[0]
		self.assertEqual(job.printer, "Kitchen printer")
		for dish in ("Steak", "Salad", "Wine"):
			self.assertIn(dish, job.markup)
		self.assertNotIn("Tiramisu", job.markup)
		self.assertNotIn("Soup", job.markup)

	def test_a_copy_carries_the_ticket_number_and_no_price(self):
		self.doc.posa_ticket_no = 7
		self.doc.items[0].rate = 38.5

		self.copy()

		markup = self.queue.jobs[0].markup
		self.assertIn("Table 5-0007", markup)
		self.assertNotIn("38.5", markup)

	def test_a_copy_goes_to_the_printer_of_the_station_and_expires_like_a_bill_does(self):
		self.copy(station="Bar")

		job = self.queue.jobs[0]
		self.assertEqual(job.printer, "Bar printer")
		self.assertEqual(job.valid_seconds, jobs.RECEIPT_VALID_MINUTES * 60, "an old copy must not come out later")
		self.assertEqual(job.reference_doctype, "Sales Invoice")
		self.assertEqual(job.reference_name, self.doc.name)

	def test_the_answer_tells_the_screen_where_to_follow_the_copy(self):
		self.printers = [printer("Kitchen printer", ["Kitchen"], online=0)]

		answer = self.copy()

		ticket = answer["tickets"][0]
		self.assertEqual(ticket["job_token"], "tok-1")
		self.assertEqual(ticket["printer"], "Kitchen printer")
		self.assertFalse(ticket["printer_online"], "the screen says the printer is off")
		self.assertEqual(answer["valid_minutes"], jobs.RECEIPT_VALID_MINUTES)
		self.assertFalse(answer["duplicate"])

	def test_with_no_station_named_each_station_that_has_a_printer_prints_its_own_lines(self):
		answer = self.copy(station=None)

		self.assertEqual(sorted(t["station"] for t in answer["tickets"]), ["Bar", "Kitchen"])
		by_printer = {job.printer: job.markup for job in self.queue.jobs}
		self.assertIn("Steak", by_printer["Kitchen printer"])
		self.assertNotIn("Wine", by_printer["Kitchen printer"])
		self.assertIn("Wine", by_printer["Bar printer"])
		self.assertNotIn("Steak", by_printer["Bar printer"])

	def test_with_no_station_named_the_whole_order_is_printed_once_on_each_printer(self):
		# the kitchen printer serves two stations: it prints the order once, not once per station
		self.printers = [printer("Kitchen printer", ["Kitchen", "Pastry"]), BAR_PRINTER]
		self.doc.items.append(line(item_name="Cake", preparation_station="Pastry"))

		answer = self.copy(station=None, whole_order=True)

		self.assertEqual(sorted(job.printer for job in self.queue.jobs), ["Bar printer", "Kitchen printer"])
		self.assertEqual(len(answer["tickets"]), 2)
		for job in self.queue.jobs:
			self.assertIn("Steak", job.markup)
			self.assertIn("Wine", job.markup)


class TestACopyWithNothingToPrintOn(KitchenCopyCase):
	def test_a_station_with_no_printer_queues_nothing_and_the_answer_says_so(self):
		self.printers = [BAR_PRINTER]

		answer = self.copy(station="Kitchen")

		self.assertEqual(self.queue.jobs, [])
		self.assertEqual(answer["tickets"], [])
		self.assertEqual(answer["no_printer"], ["Kitchen"], "the button of that station hides itself")

	def test_a_site_with_no_printer_at_all_queues_nothing(self):
		self.printers = []

		answer = self.copy(station=None)

		self.assertEqual(self.queue.jobs, [])
		self.assertEqual(answer["tickets"], [])
		self.assertEqual(answer["no_printer"], ["Bar", "Kitchen"])

	def test_a_station_with_nothing_at_the_kitchen_prints_nothing_and_has_a_printer(self):
		self.doc.items = [line(item_name="Wine", preparation_station="Bar")]

		answer = self.copy(station="Kitchen")

		self.assertEqual(self.queue.jobs, [])
		self.assertEqual(answer["tickets"], [])
		self.assertEqual(answer["no_printer"], [], "it has a printer: the button stays")


class TestAnImpatientTap(KitchenCopyCase):
	def test_a_double_tap_prints_once(self):
		first = self.copy()
		second = self.copy()

		self.assertEqual(len(self.queue.jobs), 1)
		self.assertFalse(first["duplicate"])
		self.assertTrue(second["duplicate"])
		self.assertEqual(second["tickets"], first["tickets"], "the second tap follows the first one's job")

	def test_two_screens_tapping_together_print_once(self):
		# the runner's tap and the kitchen's, on the same station's ticket within seconds: same copy
		self.copy(whole_order=False)
		self.copy(whole_order=False, mark_copy=False)

		self.assertEqual(len(self.queue.jobs), 1)

	def test_another_station_or_the_whole_order_is_another_copy(self):
		self.copy(station="Kitchen")
		self.copy(station="Bar")
		self.copy(station="Kitchen", whole_order=True)

		self.assertEqual(len(self.queue.jobs), 3)

	def test_another_order_is_another_copy(self):
		self.copy()
		self.doc = an_order()
		self.copy()

		self.assertEqual(len(self.queue.jobs), 2)

	def test_a_copy_asked_for_again_after_the_window_prints_again(self):
		with patch.object(kitchen_ticket, "COPY_WINDOW_SECONDS", 1):
			self.copy()
			time.sleep(1.3)
			second = self.copy()

		self.assertEqual(len(self.queue.jobs), 2)
		self.assertFalse(second["duplicate"])

	def test_a_print_that_failed_does_not_lock_the_tap_out(self):
		def broken(*args, **kwargs):
			raise frappe.ValidationError("printer gone")

		with patch("cloudprnt.kitchen_ticket.jobs.enqueue", broken), self.assertRaises(frappe.ValidationError):
			self.copy()

		self.copy()

		self.assertEqual(len(self.queue.jobs), 1, "the next tap prints")


class TestACopyIsNotASend(KitchenCopyCase):
	def sends_and_remembers(self):
		"""The normal send of the order, with the queue's own bookkeeping of what was printed."""
		with patch("cloudprnt.kitchen_ticket.printed_quantities", self.printed):
			on_kitchen_send(self.doc.name, 1)

	def printed(self, invoice):
		printed = {}
		for rows in self.queue.printed(invoice):
			for key, qty in json.loads(rows).items():
				printed[key] = printed.get(key, 0) + qty
		return printed

	def test_a_copy_marks_no_line_as_printed(self):
		self.copy()
		self.copy(station="Bar")

		for job in self.queue.jobs:
			self.assertFalse(job.get("kitchen_rows"), "a copy remembers no quantity")
		self.assertEqual(self.printed(self.doc.name), {})

	def test_the_next_send_prints_what_it_always_would_have_after_a_copy(self):
		self.copy()
		self.copy(station=None, whole_order=True)
		copies = len(self.queue.jobs)

		self.sends_and_remembers()

		sent = [job for job in self.queue.jobs[copies:]]
		by_printer = {job.printer: job.markup for job in sent}
		self.assertIn("2 x Steak", by_printer["Kitchen printer"], "the copy did not use up the steak")
		self.assertIn("Wine", by_printer["Bar printer"])
		self.assertNotIn("COPY", by_printer["Kitchen printer"])

	def test_a_copy_between_two_sends_changes_nothing_in_the_second(self):
		self.sends_and_remembers()
		self.copy()
		self.doc.items.append(line(item_name="Burger", qty=3))
		before = len(self.queue.jobs)

		self.sends_and_remembers()

		second = self.queue.jobs[before:]
		self.assertEqual(len(second), 1, "only the dish added since the first send is new")
		self.assertIn("3 x Burger", second[0].markup)
		self.assertNotIn("Steak", second[0].markup)


class TestFollowingACopy(FrappeTestCase):
	def status(self, row, printer_row, invoice="ACC-SINV-1", job="tok-1"):
		with (
			patch("cloudprnt.kitchen_ticket.frappe.db.get_value", return_value=row) as read,
			patch("cloudprnt.kitchen_ticket.get_printer", return_value=printer_row),
		):
			answer = kitchen_copy_status(job, invoice)
		return answer, read

	def test_the_status_of_a_copy_is_its_queue_status_and_whether_its_printer_is_on(self):
		row = frappe._dict(status="Printed", printer="Kitchen printer", result_code="")

		answer, read = self.status(row, KITCHEN_PRINTER)

		self.assertEqual(answer, {"status": "Printed", "printer_online": True, "reason": ""})
		filters = read.call_args.args[1]
		self.assertEqual(filters, {"job_token": "tok-1", "reference_doctype": "Sales Invoice", "reference_name": "ACC-SINV-1"})

	def test_a_failed_copy_says_why(self):
		row = frappe._dict(status="Failed", printer="Kitchen printer", result_code="Paper empty")

		answer, _read = self.status(row, printer("Kitchen printer", ["Kitchen"], online=0))

		self.assertEqual(answer, {"status": "Failed", "printer_online": False, "reason": "Paper empty"})

	def test_a_job_that_is_not_a_copy_of_this_order_is_unknown(self):
		answer, _read = self.status(None, None)

		self.assertIsNone(answer)

	def test_a_receipt_printer_that_serves_no_station_is_not_a_kitchen_printer(self):
		row = frappe._dict(status="Printed", printer="Till", result_code="")

		answer, _read = self.status(row, printer("Till", []))

		self.assertIsNone(answer, "the status of a till's receipt is the till's business")


class TestWhichStationsPrint(KitchenCopyCase):
	def test_the_stations_with_a_printer_are_those_the_printers_are_set_for(self):
		self.printers = [printer("A", ["Kitchen", "Pastry"]), printer("B", ["Bar"]), printer("Till", [])]

		self.assertEqual(stations_with_a_printer(), ["Bar", "Kitchen", "Pastry"])

	def test_no_printer_no_station(self):
		self.printers = []

		self.assertEqual(stations_with_a_printer(), [])


class TestTheHooks(FrappeTestCase):
	def test_the_app_listens_to_the_three_moments_pos_next_names(self):
		self.assertIn("cloudprnt.kitchen_ticket.on_kitchen_reprint", frappe.get_hooks("pos_next_kitchen_reprint"))
		self.assertIn("cloudprnt.kitchen_ticket.kitchen_copy_status", frappe.get_hooks("pos_next_kitchen_reprint_status"))
		self.assertIn("cloudprnt.kitchen_ticket.stations_with_a_printer", frappe.get_hooks("pos_next_kitchen_printers"))
