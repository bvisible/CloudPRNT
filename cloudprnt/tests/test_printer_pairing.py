"""Pairing a printer issues the printer's own account, and touches no other (audit of 06.11).

The account of a printer row is the server's to write: a save of the settings keeps none for a new row, and the
credentials of a row are issued, renewed and withdrawn on the row's own account only, whatever the row names.

These tests play the people who act: the client's administrator pairs a printer, and the printer signs in with the
key and secret it was given (HTTP Basic, as a Star printer sends them) and prints its job; a row that names another
account gets the printer's own account, and the other account keeps its roles, its key and its access.
Administrator only prepares the scenery.
"""

import base64
import json
from types import SimpleNamespace

import frappe
from frappe.auth import validate_auth
from frappe.tests.roles import as_role
from frappe.tests.utils import FrappeTestCase
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from cloudprnt import devices, protocol
from cloudprnt import print_queue_manager as jobs
from cloudprnt.cloudprnt.doctype.cloudprnt_settings import cloudprnt_settings
from cloudprnt.printers import SETTINGS, get_printer, update_printer

VICTIM = "_test_cloudprnt_colleague@example.invalid"
MAC = "00:11:62:AA:20:01"


def signed_in_printer_request(key, secret, method, query=None, body=None):
	"""What a paired Star printer sends: its key and secret as HTTP Basic, signed in by Frappe as a request is."""
	token = base64.b64encode(f"{key}:{secret}".encode()).decode()
	builder = EnvironBuilder(
		method=method,
		path="/api/method/cloudprnt.protocol.serve",
		query_string=query,
		data=json.dumps(body) if body is not None else None,
		content_type="application/json" if body is not None else None,
		headers={"Authorization": f"Basic {token}"},
		environ_base={"REMOTE_ADDR": "192.0.2.20"},
	)
	frappe.local.request = Request(builder.get_environ())
	frappe.local.request_ip = "192.0.2.20"
	frappe.set_user("Guest")
	login_manager = getattr(frappe.local, "login_manager", None)
	frappe.local.login_manager = SimpleNamespace(user="Guest")  # what a request without a session cookie holds
	try:
		validate_auth()
		return frappe.session.user, protocol.serve()
	finally:
		frappe.local.login_manager = login_manager
		frappe.set_user("Administrator")


class TestPrinterPairing(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		if not frappe.db.exists("User", VICTIM):
			user = frappe.get_doc(
				{
					"doctype": "User",
					"email": VICTIM,
					"first_name": "_cloudprnt colleague",
					"send_welcome_email": 0,
					"roles": [{"role": "Sales User"}],
				}
			)
			user.flags.skip_drive_setup = True
			user.insert(ignore_permissions=True)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		frappe.delete_doc("User", VICTIM, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		self.rows = []
		# The colleague as each test finds them: a desk account with their role, their API key, enabled.
		colleague = frappe.get_doc("User", VICTIM)
		colleague.set("roles", [{"role": "Sales User"}])
		colleague.update({"enabled": 1, "user_type": "System User"})
		colleague.save(ignore_permissions=True)
		frappe.db.set_value("User", VICTIM, "api_key", "_test_colleague_key")

	def tearDown(self):
		frappe.set_user("Administrator")
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

	def colleague(self):
		return frappe.db.get_value("User", VICTIM, ["enabled", "api_key", "user_type"], as_dict=True), sorted(
			frappe.get_all("Has Role", filters={"parent": VICTIM, "parenttype": "User"}, pluck="role")
		)

	def stored_row(self, label, **values):
		"""A printer row as a save of the settings stores it (by Administrator, the scenery)."""
		settings = frappe.get_doc(SETTINGS)
		row = settings.append("printers", {"label": label})
		settings.save()
		self.rows.append(row.name)
		if values:
			update_printer(row.name, values)
		return row.name

	def test_a_save_never_gives_a_new_row_an_account(self):
		"""A desk account of the shop (the settings let every desk account write) sends a new row naming Administrator."""
		settings = frappe.get_doc(SETTINGS)
		settings.append(
			"printers",
			{"label": "_Test CloudPRNT forged", "device_user": "Administrator", "broker_account": "x", "online": 1},
		)
		settings._keep_what_printers_reported()
		forged = settings.printers[-1]
		self.assertFalse(forged.device_user, "the account of a new row is the server's to write")
		self.assertFalse(forged.broker_account)
		self.assertFalse(forged.online)

		with as_role("sales_user"):
			settings = frappe.get_doc(SETTINGS)
			row = settings.append("printers", {"label": "_Test CloudPRNT forged", "device_user": VICTIM})
			settings.save()
		self.rows.append(row.name)
		self.assertFalse(get_printer(row.name).device_user)

	def test_pairing_a_row_that_names_a_colleague_leaves_the_colleague_alone(self):
		before = self.colleague()
		row = self.stored_row("_Test CloudPRNT forged", device_user=VICTIM)
		with as_role("sales_user"):
			credentials = cloudprnt_settings.pair_printer(printer=row)
		self.assertEqual(self.colleague(), before, "the colleague keeps their roles, key and access")
		account = devices.account_of(row)
		self.assertEqual(get_printer(row).device_user, account)
		self.assertEqual(frappe.db.get_value("User", account, "api_key"), credentials["user"])

	def test_removing_a_row_that_names_a_colleague_does_not_disable_them(self):
		before = self.colleague()
		row = self.stored_row("_Test CloudPRNT forged", device_user=VICTIM)
		with as_role("client_admin"):
			settings = frappe.get_doc(SETTINGS)
			settings.set("printers", [r for r in settings.printers if r.name != row])
			settings.save()
		self.rows.remove(row)
		self.assertEqual(self.colleague(), before)

	def test_the_administrator_of_the_client_pairs_a_printer_that_then_prints(self):
		with as_role("client_admin"):
			credentials = cloudprnt_settings.pair_printer(label="_Test CloudPRNT till")
		row = credentials["printer"]
		self.rows.append(row)
		account = devices.account_of(row)
		user = frappe.get_doc("User", account)
		roles = {r.role for r in user.roles}
		self.assertEqual(user.user_type, "Website User", "a printer opens no desk and takes no seat")
		self.assertIn(devices.ROLE, roles)
		self.assertFalse(roles & {"System Manager", "Administrator", "Admin", "Sales User"}, roles)
		self.assertEqual(get_printer(row).device_user, account)

		# The printer signs in with what it was configured with, takes the MAC it polls with and prints.
		update_printer(row, {"mac_address": None, "last_seen": "2026-01-01 00:00:00"})
		signed_in_as, response = signed_in_printer_request(
			credentials["user"], credentials["password"], "POST", body={"printerMAC": MAC, "statusCode": "200%20OK"}
		)
		self.assertEqual(signed_in_as, account)
		self.assertEqual(response.status_code, 200)
		jobs.enqueue(get_printer(row), "[align: centre]Receipt\n[cut: feed; partial]", token="_test-paired-job")
		_, response = signed_in_printer_request(
			credentials["user"], credentials["password"], "POST", body={"printerMAC": MAC, "statusCode": "200%20OK"}
		)
		answer = json.loads(response.get_data(as_text=True))
		self.assertEqual(answer.get("jobToken"), "_test-paired-job")
		_, response = signed_in_printer_request(
			credentials["user"],
			credentials["password"],
			"GET",
			query={"mac": MAC, "type": "text/vnd.star.markup", "token": "_test-paired-job"},
		)
		self.assertEqual(response.status_code, 200)
		self.assertIn(b"Receipt", response.get_data())

	def test_renewing_the_credentials_of_a_paired_printer_keeps_its_account(self):
		with as_role("client_admin"):
			first = cloudprnt_settings.pair_printer(label="_Test CloudPRNT renewed")
			row = first["printer"]
			self.rows.append(row)
			second = cloudprnt_settings.pair_printer(printer=row)
		self.assertEqual(get_printer(row).device_user, devices.account_of(row))
		self.assertNotEqual(first["password"], second["password"])
		self.assertEqual(frappe.db.get_value("User", devices.account_of(row), "api_key"), second["user"])
