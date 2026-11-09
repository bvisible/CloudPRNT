"""A printer named in the call is one the caller may use: the receipt goes to the printer of the till the sale was
made at, the drawer opens for the cashier of the till the printer is set for (maintenance#2381, after #2079)."""

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from cloudprnt.api import open_cash_drawer, print_pos_invoice, print_receipt


def printer(name, label, pos_profile=None, preparation_station=None):
	return frappe._dict(
		name=name, label=label, paper_width=80, pos_profile=pos_profile, preparation_station=preparation_station
	)


TILL = printer("P1", "Till printer", pos_profile="Till A")
OTHER_TILL = printer("P2", "Other till printer", pos_profile="Till B")
KITCHEN = printer("P3", "Pass", preparation_station="Kitchen")
NAMED_BY_PROFILE = printer("P4", "Named by the profile")
ROWS = (TILL, OTHER_TILL, KITCHEN, NAMED_BY_PROFILE)


def find(label=None):
	return {row.label: row for row in ROWS}[label]


def open_shift_at(tills):
	"""What `frappe.db.get_value("POS Opening Shift", filters, "name")` answers when the caller has a session at `tills`."""

	def get_value(doctype, filters, fieldname=None, **kwargs):
		return "SHIFT" if doctype == "POS Opening Shift" and filters.get("pos_profile") in tills else None

	return get_value


class TestTheReceiptANamedPrinterPrints(FrappeTestCase):
	"""print_receipt and print_pos_invoice: the printer a caller names is the one set for the till of the sale."""

	def print_on(
		self, printer_label, pos_profile="Till A", profile_printer=None, ticked=True, has_field=True, via="print_receipt"
	):
		"""The call, on a site where the POS Profile names `profile_printer` in its custom fields (`ticked`: the
		checkbox beside it), or has no such fields (`has_field` false)."""
		doctype = "POS Invoice" if via == "print_pos_invoice" else "Sales Invoice"
		doc = frappe._dict(doctype=doctype, name="INV-1", pos_profile=pos_profile)
		profile = frappe._dict(cloudprnt_printer=1 if ticked else 0, cloudprnt_printer_name=profile_printer)
		with (
			patch("cloudprnt.api.guards.require"),
			patch.object(frappe.db, "exists", return_value=True),
			patch.object(frappe.db, "get_value", return_value=profile),
			patch.object(frappe, "get_doc", return_value=doc),
			patch.object(frappe, "get_meta", return_value=SimpleNamespace(has_field=lambda field: has_field)),
			patch("cloudprnt.api.find_printer", side_effect=find),
			patch("cloudprnt.api.all_printers", return_value=list(ROWS)),
			patch("cloudprnt.api._queue_receipt", return_value={"success": True}) as queue,
		):
			try:
				if via == "print_pos_invoice":
					answer = print_pos_invoice(doc.name, printer_label)
				else:
					answer = print_receipt(doctype, doc.name, printer_label)
			except frappe.PermissionError:
				answer = None
		return answer, queue

	def test_a_printer_of_another_till_gets_no_receipt(self):
		answer, queue = self.print_on("Other till printer")
		self.assertIsNone(answer, "the call is refused")
		queue.assert_not_called()

	def test_a_printer_set_for_no_till_gets_no_receipt(self):
		answer, queue = self.print_on("Pass")
		self.assertIsNone(answer)
		queue.assert_not_called()

	def test_the_printer_of_the_till_the_sale_was_made_at_prints_it(self):
		answer, queue = self.print_on("Till printer")
		self.assertEqual(answer, {"success": True})
		self.assertEqual(queue.call_args.args[1], TILL)

	def test_the_printer_the_pos_profile_names_prints_it(self):
		"""The desk's own point of sale passes the printer its POS Profile names; that keeps working."""
		answer, queue = self.print_on("Named by the profile", profile_printer="P4")
		self.assertEqual(answer, {"success": True})
		self.assertEqual(queue.call_args.args[1], NAMED_BY_PROFILE)

	def test_a_printer_the_profile_does_not_name_does_not_print_it(self):
		for kwargs in (
			{"profile_printer": "P1"},
			{"profile_printer": "P4", "ticked": False},
			{"profile_printer": "P4", "has_field": False},
		):
			with self.subTest(**kwargs):
				answer, queue = self.print_on("Named by the profile", **kwargs)
				self.assertIsNone(answer)
				queue.assert_not_called()

	def test_a_sale_made_at_no_till_has_no_printer_to_name(self):
		for label in ("Till printer", "Pass"):
			with self.subTest(printer=label):
				answer, queue = self.print_on(label, pos_profile=None)
				self.assertIsNone(answer, "no till on either side is not the same till")
				queue.assert_not_called()

	def test_without_a_printer_the_receipt_goes_where_it_always_went(self):
		answer, queue = self.print_on(None)
		self.assertEqual(answer, {"success": True})
		self.assertEqual(queue.call_args.args[1], TILL)

	def test_the_pos_invoice_button_follows_the_same_rule(self):
		answer, queue = self.print_on("Other till printer", via="print_pos_invoice")
		self.assertIsNone(answer)
		queue.assert_not_called()
		answer, queue = self.print_on("Till printer", via="print_pos_invoice")
		self.assertEqual(answer, {"success": True})
		self.assertEqual(queue.call_args.args[1], TILL)


class TestTheDrawerANamedPrinterOpens(FrappeTestCase):
	"""open_cash_drawer: the drawer of a till opens for its cashier, and for a System Manager (the settings form)."""

	def open_on(self, printer_label, roles=("Sales User",), sessions=("Till A",), pos_next=True):
		with (
			patch("cloudprnt.api.guards.require"),
			patch("cloudprnt.api.find_printer", side_effect=find),
			patch.object(frappe, "get_roles", return_value=list(roles)),
			patch.object(frappe.db, "exists", return_value=pos_next),
			patch.object(frappe.db, "get_value", side_effect=open_shift_at(sessions)),
			patch("cloudprnt.api.jobs.enqueue", return_value={"job_token": "drawer-1"}) as enqueue,
			patch("cloudprnt.api.drawer._record") as record,
		):
			try:
				answer = open_cash_drawer(printer_label)
			except frappe.PermissionError:
				answer = None
		return answer, enqueue, record

	def test_the_drawer_of_another_till_stays_shut(self):
		answer, enqueue, record = self.open_on("Other till printer")
		self.assertIsNone(answer, "the cashier has no session at that till")
		enqueue.assert_not_called()
		record.assert_not_called()

	def test_the_drawer_of_a_printer_set_for_no_till_stays_shut(self):
		answer, enqueue, record = self.open_on("Pass")
		self.assertIsNone(answer)
		enqueue.assert_not_called()

	def test_the_cashier_of_the_till_opens_its_drawer(self):
		answer, enqueue, record = self.open_on("Till printer")
		self.assertTrue(answer["success"])
		self.assertEqual(enqueue.call_args.args[0], TILL)
		self.assertEqual(record.call_args.args[0], "Till A")

	def test_a_cashier_who_closed_the_session_no_longer_opens_it(self):
		answer, enqueue, record = self.open_on("Till printer", sessions=())
		self.assertIsNone(answer)
		enqueue.assert_not_called()

	def test_without_pos_next_the_printer_must_still_be_a_tills(self):
		answer, enqueue, record = self.open_on("Pass", sessions=(), pos_next=False)
		self.assertIsNone(answer)
		enqueue.assert_not_called()
		answer, enqueue, record = self.open_on("Till printer", sessions=(), pos_next=False)
		self.assertTrue(answer["success"])

	def test_a_system_manager_tests_the_drawer_of_any_printer(self):
		for label in ("Pass", "Other till printer", "Till printer"):
			with self.subTest(printer=label):
				answer, enqueue, record = self.open_on(label, roles=("System Manager",), sessions=())
				self.assertTrue(answer["success"])
				enqueue.assert_called_once()
