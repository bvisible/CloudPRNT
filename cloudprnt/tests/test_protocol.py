"""The CloudPRNT protocol as a printer speaks it: poll, fetch, confirm, each signed in as the printer's account.

Requests are built as a printer sends them and handed to the one URL a printer is configured with
(cloudprnt.protocol.serve); the answers are read as a printer reads them.
"""

import json
import unittest

import frappe
from frappe.tests.utils import FrappeTestCase
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from cloudprnt import print_queue_manager as jobs
from cloudprnt import render
from cloudprnt.api import open_cash_drawer
from cloudprnt.cloudprnt.doctype.cloudprnt_settings.cloudprnt_settings import pair_printer
from cloudprnt.printer_discovery import get_discovered_printers
from cloudprnt.printers import get_printer, update_printer
from cloudprnt.protocol import serve

MAC = "00:11:62:AA:00:01"
OTHER_MAC = "00:11:62:AA:00:02"
STRANGER_MAC = "00:11:62:AA:00:99"
READY = "2f 8c 0 0 0 0 0 0 0 0 0 0 0 0 0"
COVER_OPEN = "2f 8c 20 0 0 0 0 0 0 0 0 0 0 0 0"
PAPER_EMPTY = "2f 8c 0 0 0 8 0 0 0 0 0 0 0 0 0"


def printer_request(method, user="Guest", query=None, body=None):
	"""What a printer sends, answered by the protocol."""
	builder = EnvironBuilder(
		method=method,
		path="/api/method/cloudprnt.protocol.serve",
		query_string=query,
		data=json.dumps(body) if body is not None else None,
		content_type="application/json" if body is not None else None,
		environ_base={"REMOTE_ADDR": "192.0.2.10"},
	)
	frappe.local.request = Request(builder.get_environ())
	frappe.local.request_ip = "192.0.2.10"
	frappe.set_user(user)
	try:
		return serve()
	finally:
		frappe.set_user("Administrator")


def poll(user, mac=MAC, status=READY, **extra):
	response = printer_request(
		"POST", user, body={"printerMAC": mac.replace(":", "."), "statusCode": "200%20OK", "status": status, **extra}
	)
	return response.status_code, (json.loads(response.get_data(as_text=True)) if response.mimetype == "application/json" else {})


class TestProtocol(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.first = pair_printer(label="_Test CloudPRNT first")
		cls.second = pair_printer(label="_Test CloudPRNT second")
		cls.first_user = get_printer(cls.first["printer"]).device_user
		cls.second_user = get_printer(cls.second["printer"]).device_user
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		names = {cls.first["printer"], cls.second["printer"]}
		settings = frappe.get_doc("CloudPRNT Settings")
		settings.set("printers", [row for row in settings.printers if row.name not in names])
		if settings.default_printer in names:
			settings.default_printer = None
		settings.save()
		frappe.db.delete(jobs.DOCTYPE, {"printer": ("in", list(names))})
		for user in (cls.first_user, cls.second_user):
			frappe.delete_doc("User", user, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		frappe.db.delete(jobs.DOCTYPE, {"printer": ("in", [self.first["printer"], self.second["printer"]])})
		frappe.cache().delete_keys("cloudprnt_")

	def bind(self):
		"""The first printer's first poll: it takes MAC and prints its connection ticket, confirmed here."""
		update_printer(self.first["printer"], {"mac_address": None, "last_seen": None})
		_, answer = poll(self.first_user)
		token = answer["jobToken"]
		printer_request("GET", self.first_user, query={"mac": MAC, "type": "text/vnd.star.markup", "token": token})
		printer_request("DELETE", self.first_user, query={"mac": MAC, "token": token, "code": "200 OK"})

	def test_an_unknown_printer_gets_no_job_and_is_shown_for_pairing(self):
		code, answer = poll("Guest", mac=STRANGER_MAC)
		self.assertEqual((code, answer), (200, {"jobReady": False}))
		macs = [entry["mac_address"] for entry in get_discovered_printers()["printers"]]
		self.assertIn(STRANGER_MAC, macs)

	def test_the_first_poll_binds_the_mac_and_prints_a_connection_ticket(self):
		update_printer(self.first["printer"], {"mac_address": None, "last_seen": None})
		code, answer = poll(self.first_user)
		self.assertEqual(code, 200)
		self.assertEqual(get_printer(self.first["printer"]).mac_address, MAC)
		self.assertTrue(answer["jobReady"])
		self.assertTrue(answer["jobToken"].startswith("connected-"))
		self.assertIn("application/vnd.star.starprnt" if render.cputil_available() else "text/vnd.star.markup", answer["mediaTypes"])

	def test_a_paired_printer_without_credentials_is_asked_for_them(self):
		self.bind()
		response = printer_request("POST", "Guest", body={"printerMAC": MAC, "statusCode": "200 OK"})
		self.assertEqual(response.status_code, 401)
		self.assertIn("Basic", response.headers.get("WWW-Authenticate", ""))
		jobs.enqueue(get_printer(self.first["printer"]), "secret receipt")
		response = printer_request("GET", "Guest", query={"mac": MAC, "type": "text/vnd.star.markup"})
		self.assertEqual(response.status_code, 401)
		self.assertNotIn(b"secret", response.get_data())

	def test_another_printers_account_gets_none_of_its_jobs(self):
		self.bind()
		update_printer(self.second["printer"], {"mac_address": OTHER_MAC, "last_seen": "2026-01-01 00:00:00"})
		jobs.enqueue(get_printer(self.first["printer"]), "secret receipt")
		code, answer = poll(self.second_user, mac=MAC)
		self.assertFalse(answer.get("jobReady"))
		response = printer_request("GET", self.second_user, query={"mac": MAC, "type": "text/vnd.star.markup"})
		self.assertEqual(response.status_code, 403)
		self.assertNotIn(b"secret", response.get_data())

	def test_a_job_is_offered_fetched_and_confirmed_printed(self):
		self.bind()
		jobs.enqueue(get_printer(self.first["printer"]), "[align: centre]Hello\n[cut: feed; partial]", token="job-printed")
		_, answer = poll(self.first_user)
		self.assertEqual(answer["jobToken"], "job-printed")
		media_type = answer["mediaTypes"][0]
		response = printer_request("GET", self.first_user, query={"mac": MAC, "type": media_type, "token": "job-printed"})
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.mimetype, media_type)
		if media_type == "application/vnd.star.starprnt":
			self.assertTrue(response.get_data().startswith(b"\x1b@"), "StarPRNT data starts by initialising the printer")
		self.assertEqual(frappe.db.get_value(jobs.DOCTYPE, {"job_token": "job-printed"}, "status"), "Fetched")
		_, answer = poll(self.first_user, printingInProgress=True, jobToken="job-printed")
		self.assertFalse(answer["jobReady"], "nothing is offered while the printer prints")
		printer_request("DELETE", self.first_user, query={"mac": MAC, "token": "job-printed", "code": "200%20OK"})
		self.assertEqual(frappe.db.get_value(jobs.DOCTYPE, {"job_token": "job-printed"}, "status"), "Printed")

	def test_a_failed_print_is_recorded_as_failed(self):
		self.bind()
		jobs.enqueue(get_printer(self.first["printer"]), "Hello", token="job-failed")
		printer_request("GET", self.first_user, query={"mac": MAC, "type": "text/vnd.star.markup", "token": "job-failed"})
		printer_request("DELETE", self.first_user, query={"mac": MAC, "token": "job-failed", "code": "520 Printer Error"})
		self.assertEqual(
			frappe.db.get_value(jobs.DOCTYPE, {"job_token": "job-failed"}, ["status", "result_code"]),
			("Failed", "520 Printer Error"),
		)

	def test_a_job_leaves_only_in_a_format_it_was_offered_in(self):
		self.bind()
		jobs.enqueue(get_printer(self.first["printer"]), "Hello", token="job-format")
		response = printer_request("GET", self.first_user, query={"mac": MAC, "type": "application/x-sh", "token": "job-format"})
		self.assertEqual(response.status_code, 415)

	def test_the_printer_is_asked_what_it_is_and_its_answers_are_kept(self):
		self.bind()
		update_printer(self.first["printer"], {"client_type": None, "media_types": None, "paper_width": 0, "print_dots": 0})
		frappe.cache().delete_keys("cloudprnt_")
		_, answer = poll(self.first_user)
		asked = {action["request"] for action in answer.get("clientAction", [])}
		self.assertTrue({"ClientType", "Encodings", "PageInfo"} <= asked, answer)
		poll(
			self.first_user,
			clientAction=[
				{"request": "ClientType", "result": "Star mC-Print3"},
				{"request": "ClientVersion", "result": "5.3"},
				{"request": "Encodings", "result": "application/vnd.star.starprnt; text/vnd.star.markup"},
				{"request": "PageInfo", "result": {"paperWidth": "80", "printWidth": "72", "horizontalResolution": "8"}},
			],
		)
		row = get_printer(self.first["printer"])
		self.assertEqual((row.client_type, row.client_version, row.paper_width, row.print_dots), ("Star mC-Print3", "5.3", 80, 576))
		self.assertEqual(json.loads(row.media_types), ["application/vnd.star.starprnt", "text/vnd.star.markup"])
		_, answer = poll(self.first_user)
		self.assertNotIn("clientAction", answer, "a printer that answered is not asked again")

	@unittest.skipUnless(render.cputil_available(), "cputil runs on linux x86-64 only")
	def test_the_printer_status_is_read(self):
		self.bind()
		frappe.cache().delete_keys("cloudprnt_")
		poll(self.first_user, status=COVER_OPEN)
		row = get_printer(self.first["printer"])
		self.assertEqual((row.cover_open, row.status), (1, frappe._("Cover open")))
		poll(self.first_user, status=PAPER_EMPTY)
		row = get_printer(self.first["printer"])
		self.assertEqual((row.cover_open, row.paper_empty, row.status), (0, 1, frappe._("Paper empty")))

	@unittest.skipUnless(render.cputil_available(), "cputil runs on linux x86-64 only")
	def test_the_cash_drawer_opens_without_feeding_paper(self):
		self.bind()
		open_cash_drawer(self.first["printer"])
		_, answer = poll(self.first_user)
		response = printer_request(
			"GET",
			self.first_user,
			query={"mac": MAC, "type": "application/vnd.star.starprnt", "token": answer["jobToken"]},
		)
		data = response.get_data()
		self.assertIn(b"\x1b\x07", data, "the drawer pulse is set (ESC BEL)")
		self.assertIn(b"\x07", data.split(b"\x1b\x07", 1)[1][2:], "and fired (BEL)")
		self.assertNotIn(b"\x1bd", data, "no cut, so no paper")


class TestRender(unittest.TestCase):
	def test_the_paper_width_picks_the_device_constraint(self):
		self.assertEqual(render.paper_option(print_dots=576), ["printarea", "576"])
		self.assertEqual(render.paper_option("58mm"), ["thermal58"])
		self.assertEqual(render.paper_option(80), ["thermal80"])
		self.assertEqual(render.paper_option(None), ["thermal80"])

	def test_only_listed_formats_are_rendered(self):
		with self.assertRaises(render.RenderError):
			render.render("Hello", "application/x-sh")

	def test_a_status_that_is_not_hex_never_reaches_cputil(self):
		self.assertIsNone(render.decode_status("2f; rm -rf /"))
		self.assertIsNone(render.decode_status("$(id)"))

	def test_markup_jobs_are_offered_in_what_the_printer_takes(self):
		offered = render.offered_media_types(["text/vnd.star.markup", "image/png"])
		self.assertEqual(offered, ["text/vnd.star.markup"])
		self.assertIn("text/plain", render.offered_media_types(None))
