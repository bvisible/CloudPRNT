"""
The HTTP methods of the app refuse a portal customer and let the staff through.

A Website User with no role is what anyone becomes by signing up on a site with a webshop; before 2026-10-08
such an account could call every method of this app. The staff reaches the same methods from the CloudPRNT
settings form and from the POS.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from cloudprnt.api import open_cash_drawer, print_pos_invoice, print_receipt
# Imported under other names: a function named test_* would be collected as a test.
from cloudprnt.cloudprnt.doctype.cloudprnt_settings.cloudprnt_settings import pair_printer
from cloudprnt.cloudprnt.doctype.cloudprnt_settings.cloudprnt_settings import test_print as send_test_print
from cloudprnt.print_queue_manager import print_again
from cloudprnt.printer_discovery import add_discovered_printer, get_discovered_printers

PORTAL = "_test_cloudprnt_portal@example.invalid"
MAC = "00:11:62:00:00:01"


class TestGuards(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		if not frappe.db.exists("User", PORTAL):
			user = frappe.get_doc(
				{
					"doctype": "User",
					"email": PORTAL,
					"first_name": "_cloudprnt portal",
					"user_type": "Website User",
					"send_welcome_email": 0,
				}
			)
			user.flags.skip_drive_setup = True
			user.insert(ignore_permissions=True)

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		if frappe.db.exists("User", PORTAL):
			frappe.delete_doc("User", PORTAL, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDownClass()

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_a_portal_customer_is_refused_by_every_method(self):
		frappe.set_user(PORTAL)
		calls = {
			"print_pos_invoice": lambda: print_pos_invoice("ACC-PSINV-NO-SUCH-0001"),
			"print_receipt": lambda: print_receipt("Sales Invoice", "ACC-SINV-NO-SUCH-0001"),
			"open_cash_drawer": lambda: open_cash_drawer(MAC),
			"pair_printer": lambda: pair_printer(label="_portal printer"),
			"test_print": lambda: send_test_print(MAC),
			"print_again": lambda: print_again("no-such-job"),
			"get_discovered_printers": get_discovered_printers,
			"add_discovered_printer": lambda: add_discovered_printer(MAC),
		}
		for name, call in calls.items():
			with self.subTest(method=name), self.assertRaises(frappe.PermissionError):
				call()

	def test_the_staff_reaches_the_methods(self):
		"""Past the guard, each method answers on its own terms: nothing here is a permission refusal."""
		with self.assertRaises(frappe.DoesNotExistError):
			print_receipt("Sales Invoice", "ACC-SINV-NO-SUCH-0001")
		with self.assertRaises(frappe.ValidationError, msg="an unknown printer is reported"):
			send_test_print("no such printer label")
		with self.assertRaises(frappe.ValidationError):
			open_cash_drawer("no such printer label")
		get_discovered_printers()

	def test_a_test_print_fetches_web_links_only(self):
		answer = send_test_print("no such printer label", image_link="file:///etc/passwd")
		self.assertFalse(answer["success"])
		self.assertIn("http", answer["message"])
