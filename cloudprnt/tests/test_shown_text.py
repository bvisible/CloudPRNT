"""The texts the desk shows of a printer come out as text, never as markup (audit of 06.11).

The printers grid of the CloudPRNT settings, its alerts and the receipt button's alert insert a printer's label and
what a printer reports (its status, its model) as HTML. A label typed as a tag, or a status code a printer's account
posts as one, ran as script in the session of whoever opened the screen or printed a receipt. And an anonymous poll
stored its clientType whole in the one cache value every later unpaired poll reads and writes back.

A label keeps what a person types (letters, `&`, quotes); only what opens a tag goes. A printer's real reports
("200 OK", "Star mC-Print3") are kept as they come.
"""

import json
import re
from pathlib import Path

import frappe
from frappe.tests.roles import as_role
from frappe.tests.utils import FrappeTestCase

from cloudprnt import devices, printer_discovery
from cloudprnt import print_queue_manager as jobs
from cloudprnt.cloudprnt.doctype.cloudprnt_settings import cloudprnt_settings
from cloudprnt.printers import SETTINGS, get_printer, update_printer
from cloudprnt.tests.test_protocol import printer_request

APP = Path(__file__).resolve().parents[1]
MAC = "00:11:62:AA:30:01"
GUEST_MAC = "00:11:62:AA:30:99"
TAG = '"<img src=x onerror=alert(1)>"'


class TestShownText(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.cache().delete_keys("cloudprnt_")
		self.rows = []

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.cache().delete_keys("cloudprnt_")
		if self.rows:
			settings = frappe.get_doc(SETTINGS)
			settings.set("printers", [row for row in settings.printers if row.name not in self.rows])
			if settings.default_printer in self.rows:
				settings.default_printer = None
			settings.save()
			frappe.db.delete(jobs.DOCTYPE, {"printer": ("in", self.rows)})
			for name in self.rows:
				if frappe.db.exists("User", devices.account_of(name)):
					frappe.delete_doc("User", devices.account_of(name), force=True, ignore_permissions=True)
		frappe.db.commit()

	def pair(self, label):
		with as_role("client_admin"):
			credentials = cloudprnt_settings.pair_printer(label=label)
		self.rows.append(credentials["printer"])
		return credentials["printer"]

	def test_a_label_keeps_what_a_person_types_and_no_tag(self):
		row = self.pair(TAG)
		label = get_printer(row).label
		self.assertNotRegex(label, "[<>]")
		self.assertIn("img src=x", label, "the rest of the text stays: it is shown, not run")

		row = self.pair("Joe's Bar & Grill")
		self.assertEqual(get_printer(row).label, "Joe's Bar & Grill")

	def test_a_label_saved_in_the_form_keeps_no_tag(self):
		row = self.pair("_Test CloudPRNT form")
		with as_role("client_admin"):
			settings = frappe.get_doc(SETTINGS)
			next(r for r in settings.printers if r.name == row).label = "<b>Till</b> 2"
			settings.save()
		self.assertEqual(get_printer(row).label, "bTill/b 2")

	def test_what_a_printer_reports_is_kept_as_text(self):
		row = self.pair("_Test CloudPRNT reports")
		account = devices.account_of(row)
		update_printer(row, {"mac_address": MAC, "last_seen": "2026-01-01 00:00:00", "online": 0})
		response = printer_request(
			"POST",
			account,
			body={
				"printerMAC": MAC,
				"statusCode": "<img src=x onerror=alert(1)>",
				"status": "not hex",
				"clientAction": [{"request": "ClientType", "result": "<svg onload=alert(1)>"}],
			},
		)
		self.assertEqual(response.status_code, 200)
		stored = get_printer(row)
		for field in ("status", "status_code", "client_type"):
			with self.subTest(field=field):
				self.assertNotRegex(stored.get(field) or "", "[<>]")

		frappe.cache().delete_keys("cloudprnt_")
		update_printer(row, {"online": 0})
		printer_request(
			"POST",
			account,
			body={
				"printerMAC": MAC,
				"statusCode": "200%20OK",
				"clientAction": [{"request": "ClientType", "result": "Star mC-Print3"}],
			},
		)
		stored = get_printer(row)
		self.assertEqual((stored.status, stored.status_code, stored.client_type), ("Ready", "200 OK", "Star mC-Print3"))

	def test_an_anonymous_poll_leaves_a_short_note(self):
		printer_request(
			"POST", "Guest", body={"printerMAC": GUEST_MAC, "statusCode": "200 OK", "clientType": "x" * 200000 + "<b>"}
		)
		stored = frappe.cache().get_value(printer_discovery.CACHE_KEY) or ""
		self.assertLess(len(stored), 2000, "one note holds a few hundred characters")
		with as_role("client_admin"):
			noted = {entry["mac_address"]: entry for entry in printer_discovery.get_discovered_printers()["printers"]}
		self.assertLessEqual(len(noted[GUEST_MAC]["client_type"]), printer_discovery.MAX_TEXT)
		self.assertNotIn("<", noted[GUEST_MAC]["client_type"])

		# A real printer not paired yet is noted as it says it is.
		printer_request("POST", "Guest", body={"printerMAC": GUEST_MAC, "statusCode": "200 OK", "clientType": "Star mC-Print3"})
		with as_role("client_admin"):
			noted = {entry["mac_address"]: entry for entry in printer_discovery.get_discovered_printers()["printers"]}
		self.assertEqual(noted[GUEST_MAC]["client_type"], "Star mC-Print3")
		self.assertEqual(noted[GUEST_MAC]["poll_count"], 2)

	def test_a_note_stored_before_the_bound_is_cut_when_read(self):
		frappe.cache().set_value(
			printer_discovery.CACHE_KEY,
			json.dumps(
				{
					GUEST_MAC: {
						"mac_address": GUEST_MAC,
						"last_seen": frappe.utils.now_datetime().isoformat(),
						"client_type": "y" * 50000,
						"poll_count": 3,
					}
				}
			),
		)
		with as_role("client_admin"):
			noted = printer_discovery.get_discovered_printers()["printers"]
		self.assertEqual(len(noted[0]["client_type"]), printer_discovery.MAX_TEXT)

	def test_the_desk_scripts_show_a_label_as_text(self):
		"""The alerts of the settings form and of the receipt button escape the server's message (it names the
		printer by its label), and the printer picker escapes the labels it lists."""
		settings_js = (APP / "cloudprnt" / "doctype" / "cloudprnt_settings" / "cloudprnt_settings.js").read_text()
		receipt_js = (APP / "public" / "js" / "receipt.js").read_text()
		for name, source in (("cloudprnt_settings.js", settings_js), ("receipt.js", receipt_js)):
			with self.subTest(script=name):
				self.assertNotRegex(source, r"show_alert\(\{\s*message:\s*r\.message", "a server message raw in an alert")
		self.assertRegex(settings_js, r"label:\s*frappe\.utils\.escape_html\(row\.label")
		self.assertRegex(settings_js, r"show_alert\(\{\s*message:\s*frappe\.utils\.escape_html\(message")
		self.assertRegex(receipt_js, r"frappe\.utils\.escape_html\(r\.message\.message")
		self.assertEqual(len(re.findall(r"show_alert\(", settings_js)), 1, "every alert of the form goes through show_message")
