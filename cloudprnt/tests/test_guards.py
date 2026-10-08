"""
The HTTP methods of the app refuse a portal customer and let the staff through.

A Website User with no role is what anyone becomes by signing up on a site with a webshop; before 2026-10-08
such an account could call every method of this app. The staff reaches the same methods from the CloudPRNT
settings form and from the POS.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from cloudprnt.api import print_pos_invoice
# Imported under another name: pytest, which this repository also runs, would collect a `test_print`.
from cloudprnt.cloudprnt.doctype.cloudprnt_settings.cloudprnt_settings import test_print as send_test_print
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
			"test_print": lambda: send_test_print(MAC),
			"get_discovered_printers": get_discovered_printers,
			"add_discovered_printer": lambda: add_discovered_printer(MAC),
		}
		for name, call in calls.items():
			with self.subTest(method=name), self.assertRaises(frappe.PermissionError):
				call()

	def test_the_staff_reaches_the_methods(self):
		"""Past the guard, each method answers on its own terms: nothing here is a permission refusal."""
		self.assertFalse(print_pos_invoice("ACC-PSINV-NO-SUCH-0001")["success"], "an unknown invoice is reported")
		self.assertFalse(send_test_print("no such printer label")["success"], "an unknown printer is reported")
		get_discovered_printers()

	def test_a_test_print_fetches_web_links_only(self):
		answer = send_test_print(MAC, image_link="file:///etc/passwd")
		self.assertFalse(answer["success"])
		self.assertIn("http", answer["message"])
