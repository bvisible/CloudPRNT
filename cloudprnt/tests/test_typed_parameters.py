"""A till session, a drawer opening and a receipt are named by text, never by a filter (audit of 06.11).

`shift_summary` checks that the cashier owns the session it is given, then counts that session's drawer openings:
both must read the same session. The parameters are typed as text, so Frappe refuses a list or a dict before the
method runs. A cashier asking about their own session, and printing a receipt at the till, get what they always got.
"""

import unittest

import frappe
from frappe.tests.roles import as_role, make_persona
from frappe.tests.utils import FrappeTestCase

from cloudprnt import api, devices, drawer
from cloudprnt import print_queue_manager as jobs
from cloudprnt.cloudprnt.doctype.cloudprnt_settings import cloudprnt_settings
from cloudprnt.printers import SETTINGS, get_printer

SHIFT = "_Test CloudPRNT shift of the cashier"
OTHER_SHIFT = "_Test CloudPRNT shift of another cashier"
HAS_POS_NEXT = bool(frappe.db.exists("DocType", "POS Opening Shift"))


@unittest.skipUnless(HAS_POS_NEXT, "POS Next is not installed")
class TestTypedParameters(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.cashier = make_persona("cashier")
		# The scenery: two till sessions (one the cashier's) and their drawer openings, written as they stand.
		for name, user in ((SHIFT, cls.cashier), (OTHER_SHIFT, "Administrator")):
			if not frappe.db.exists("POS Opening Shift", name):
				shift = frappe.new_doc("POS Opening Shift")
				shift.update({"name": name, "user": user, "status": "Open", "docstatus": 1})
				shift.db_insert()
		for shift, reason in ((SHIFT, "Give change"), (OTHER_SHIFT, "Other"), (OTHER_SHIFT, "Sale")):
			frappe.get_doc(
				{
					"doctype": drawer.DOCTYPE,
					"opened_at": frappe.utils.now_datetime(),
					"user": "Administrator",
					"pos_opening_shift": shift,
					"reason": reason,
					"printer": "_Test CloudPRNT",
					"print_job": f"_test-{frappe.generate_hash(length=8)}",
					"result": drawer.OPENED,
				}
			).insert(ignore_permissions=True, ignore_links=True)

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		frappe.db.delete(drawer.DOCTYPE, {"pos_opening_shift": ("in", [SHIFT, OTHER_SHIFT])})
		frappe.db.delete("POS Opening Shift", {"name": ("in", [SHIFT, OTHER_SHIFT])})
		frappe.db.commit()
		super().tearDownClass()

	def test_a_cashier_counts_the_openings_of_their_own_session(self):
		with as_role("cashier"):
			self.assertEqual(drawer.shift_summary(SHIFT), {"total": 1, "without_sale": 1, "not_opened": 0})

	def test_a_cashier_still_needs_the_journal_to_read_another_session(self):
		with as_role("cashier"), self.assertRaises(frappe.PermissionError):
			drawer.shift_summary(OTHER_SHIFT)


class TestNamesNotFilters(FrappeTestCase):
	"""Refused by Frappe before the method runs: no till session needs to exist."""

	def test_a_list_in_place_of_the_session_is_refused(self):
		with as_role("cashier"):
			for value in (["!=", SHIFT], ["like", "%another%", SHIFT]):
				with self.subTest(value=value), self.assertRaises(frappe.FrappeTypeError):
					drawer.shift_summary(value)
			with self.assertRaises(frappe.FrappeTypeError):
				drawer.opening_status(["!=", ""])
			with self.assertRaises(frappe.FrappeTypeError):
				drawer.open_from_till("_Test till", "Give change", pos_opening_shift=["!=", ""])


class TestReceiptAtTheTill(FrappeTestCase):
	"""A cashier prints the receipt of a sale of the till on its printer: the typed parameters change nothing."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.sale = frappe.db.get_value("Sales Invoice", {"is_pos": 1, "docstatus": 1}, "name", order_by="creation desc")

	def test_a_cashier_prints_a_receipt(self):
		if not self.sale:
			self.skipTest("no POS sale on this site")
		with as_role("client_admin"):
			credentials = cloudprnt_settings.pair_printer(label="_Test CloudPRNT receipt & co")
		row = credentials["printer"]
		try:
			with as_role("cashier"):
				answer = api.print_receipt("Sales Invoice", self.sale, printer=row)
			self.assertTrue(answer["success"])
			self.assertIn("_Test CloudPRNT receipt & co", answer["message"])
			job = frappe.db.get_value(jobs.DOCTYPE, {"job_token": answer["job_token"]}, ["printer", "reference_name"], as_dict=True)
			self.assertEqual((job.printer, job.reference_name), (get_printer(row).name, self.sale))
			with as_role("cashier"), self.assertRaises(frappe.FrappeTypeError):
				api.print_receipt("Sales Invoice", {"docstatus": 1})
		finally:
			frappe.set_user("Administrator")
			settings = frappe.get_doc(SETTINGS)
			settings.set("printers", [r for r in settings.printers if r.name != row])
			if settings.default_printer == row:
				settings.default_printer = None
			settings.save()
			frappe.db.delete(jobs.DOCTYPE, {"printer": row})
			frappe.db.delete(drawer.DOCTYPE, {"printer": "_Test CloudPRNT receipt & co"})
			frappe.delete_doc("User", devices.account_of(row), force=True, ignore_permissions=True)
			frappe.db.commit()
