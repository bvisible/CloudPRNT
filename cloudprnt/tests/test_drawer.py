"""The cash drawer from the till and its journal; the till's printing settings; kitchen tickets at each send."""

import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from cloudprnt import api, drawer, kitchen_ticket, render, till
from cloudprnt import print_queue_manager as jobs
from cloudprnt.cloudprnt.doctype.cloudprnt_settings.cloudprnt_settings import pair_printer
from cloudprnt.printers import get_printer, update_printer
from cloudprnt.tests.test_protocol import MAC, printer_request

PROFILE = "_Test CloudPRNT Till"


class TestCashDrawer(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.printer = pair_printer(label="_Test CloudPRNT drawer")
		cls.user = get_printer(cls.printer["printer"]).device_user
		update_printer(cls.printer["printer"], {"mac_address": MAC, "last_seen": "2026-01-01 00:00:00", "pos_profile": PROFILE})
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		settings = frappe.get_doc("CloudPRNT Settings")
		settings.set("printers", [row for row in settings.printers if row.name != cls.printer["printer"]])
		if settings.default_printer == cls.printer["printer"]:
			settings.default_printer = None
		settings.save()
		frappe.db.delete(jobs.DOCTYPE, {"printer": cls.printer["printer"]})
		frappe.db.delete(drawer.DOCTYPE, {"pos_profile": PROFILE})
		frappe.delete_doc("User", cls.user, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		frappe.db.delete(jobs.DOCTYPE, {"printer": self.printer["printer"]})
		frappe.db.delete(drawer.DOCTYPE, {"pos_profile": PROFILE})
		frappe.cache().delete_keys("cloudprnt_")

	def take_and_confirm(self, token, code="200 OK"):
		"""The printer fetches job `token` as StarPRNT (or markup without cputil) and confirms it."""
		media_type = "application/vnd.star.starprnt" if render.cputil_available() else "text/vnd.star.markup"
		printer_request("GET", self.user, query={"mac": MAC, "type": media_type, "token": token})
		printer_request("DELETE", self.user, query={"mac": MAC, "token": token, "code": code})

	def test_the_drawer_needs_a_reason_and_a_note_for_other(self):
		with patch.object(drawer, "_open_session", return_value="SHIFT-1"):
			with self.assertRaises(frappe.ValidationError):
				drawer.open_from_till(PROFILE, "Because")
			with self.assertRaises(frappe.ValidationError):
				drawer.open_from_till(PROFILE, "Other", note="  ")

	@unittest.skipUnless(frappe.db.exists("DocType", "POS Opening Shift"), "POS Next is not installed")
	def test_only_a_cashier_with_an_open_session_opens_the_drawer(self):
		with self.assertRaises(frappe.PermissionError):
			drawer.open_from_till(PROFILE, "Give change")

	def test_an_opening_is_journaled_and_follows_the_printer(self):
		with patch.object(drawer, "_open_session", return_value="SHIFT-1"):
			answer = drawer.open_from_till(PROFILE, "Give change")
		opening = frappe.get_doc(drawer.DOCTYPE, answer["opening"])
		self.assertEqual((opening.reason, opening.result, opening.pos_opening_shift), ("Give change", "Sent", "SHIFT-1"))
		job = frappe.db.get_value(jobs.DOCTYPE, {"job_token": opening.print_job}, ["open_drawer", "expires_at", "creation"], as_dict=True)
		self.assertEqual(job.open_drawer, "start")
		self.assertLessEqual((job.expires_at - job.creation).total_seconds(), jobs.DRAWER_VALID_SECONDS + 1)
		self.take_and_confirm(opening.print_job)
		self.assertEqual(drawer.opening_status(opening.name)["result"], "Opened")

	def test_a_receipt_too_late_for_the_drawer_journals_it_not_opened(self):
		row = get_printer(self.printer["printer"])
		result = jobs.enqueue(row, "Receipt", open_drawer="end", token="drawer-late-receipt")
		drawer._record(PROFILE, drawer.SALE, "", "SHIFT-1", row, result["job_token"])
		frappe.db.set_value(jobs.DOCTYPE, {"job_token": "drawer-late-receipt"}, "creation", add_to_date(now_datetime(), minutes=-2))
		self.take_and_confirm("drawer-late-receipt")
		result, detail = frappe.db.get_value(drawer.DOCTYPE, {"print_job": "drawer-late-receipt"}, ["result", "result_detail"])
		self.assertEqual(result, "Not opened")
		self.assertTrue(detail)

	def test_an_expired_opening_is_journaled_not_opened(self):
		with patch.object(drawer, "_open_session", return_value="SHIFT-1"):
			opening = drawer.open_from_till(PROFILE, "Cash float")["opening"]
		token = frappe.db.get_value(drawer.DOCTYPE, opening, "print_job")
		frappe.db.set_value(jobs.DOCTYPE, {"job_token": token}, "expires_at", add_to_date(now_datetime(), minutes=-1))
		jobs.expire_jobs()
		self.assertEqual(frappe.db.get_value(drawer.DOCTYPE, opening, "result"), "Not opened")

	def test_the_session_closing_counts_the_openings(self):
		row = get_printer(self.printer["printer"])
		with patch.object(drawer, "_open_session", return_value="SHIFT-2"):
			drawer.open_from_till(PROFILE, "Give change")
			drawer.open_from_till(PROFILE, "Other", note="A 50 note changed")
		sale = jobs.enqueue(row, "Receipt", open_drawer="end")
		drawer._record(PROFILE, drawer.SALE, "", "SHIFT-2", row, sale["job_token"])
		summary = drawer.shift_summary("SHIFT-2")
		self.assertEqual(summary, {"total": 3, "without_sale": 2, "not_opened": 0})

	def test_a_sale_opens_the_drawer_once_and_its_reprint_leaves_it_shut(self):
		sale = _cash_sale("ACC-SINV-TEST-0101")
		update_printer(self.printer["printer"], {"auto_print": 0, "open_drawer": "Cash payments"})
		with patch.object(api, "is_cash_sale", return_value=True), patch.object(api, "get_receipt_markup", return_value="Receipt"):
			api.print_on_submit(sale)
			opening = frappe.get_all(drawer.DOCTYPE, filters={"reference_name": sale.name}, fields=["reason", "print_job"])
			self.assertEqual([row.reason for row in opening], ["Sale"], "no receipt by itself: the drawer still opens")
			self.assertEqual(frappe.db.get_value(jobs.DOCTYPE, {"job_token": opening[0].print_job}, "open_drawer"), "start")
			api._queue_receipt(sale, get_printer(self.printer["printer"]))
		receipt = frappe.db.get_value(jobs.DOCTYPE, {"reference_name": sale.name}, "open_drawer")
		self.assertFalse(receipt, "a receipt printed after the sale leaves the drawer shut")
		self.assertEqual(frappe.db.count(drawer.DOCTYPE, {"reference_name": sale.name}), 1)

	def test_a_sale_synced_from_an_offline_till_neither_prints_nor_opens(self):
		sale = _cash_sale("ACC-SINV-TEST-0102")
		sale.flags.pos_next_offline_sync = True
		update_printer(self.printer["printer"], {"auto_print": 1, "open_drawer": "Always"})
		try:
			api.print_on_submit(sale)
		finally:
			update_printer(self.printer["printer"], {"auto_print": 0, "open_drawer": "Cash payments"})
		self.assertFalse(frappe.db.exists(jobs.DOCTYPE, {"printer": self.printer["printer"]}))
		self.assertFalse(frappe.db.exists(drawer.DOCTYPE, {"reference_name": sale.name}))


def _cash_sale(name):
	"""A POS sale of the test till, as the on_submit hook reads it."""
	return frappe._dict(
		doctype="Sales Invoice",
		name=name,
		is_pos=1,
		pos_profile=PROFILE,
		posa_pos_opening_shift="SHIFT-3",
		flags=frappe._dict(),
	)


class TestTillPrinting(FrappeTestCase):
	def test_the_till_reads_its_printer_and_its_drawer_rule(self):
		pair = pair_printer(label="_Test CloudPRNT till read")
		try:
			update_printer(pair["printer"], {"pos_profile": PROFILE, "auto_print": 1, "open_drawer": "Always"})
			answer = till.get_till_printing(PROFILE)
			self.assertEqual((answer["receipt_printer"], answer["auto_print"], answer["open_drawer"]), (pair["printer"], 1, "Always"))
			self.assertIn(pair["printer"], [row["name"] for row in answer["printers"]])
			self.assertNotIn("device_user", answer["printers"][0], "the till never sees a printer's account")
		finally:
			_remove_rows([pair["printer"]])

	@unittest.skipUnless(frappe.db.get_value("POS Profile", {}, "name"), "no POS Profile on this site")
	def test_the_till_moves_its_receipts_to_another_printer_and_shares_one_between_stations(self):
		profile = frappe.db.get_value("POS Profile", {}, "name")
		first = pair_printer(label="_Test CloudPRNT till first")
		second = pair_printer(label="_Test CloudPRNT till second")
		try:
			till.set_till_printing(profile, receipt_printer=first["printer"], auto_print=1, open_drawer="Never")
			till.set_till_printing(profile, receipt_printer=second["printer"], auto_print=1)
			self.assertFalse(get_printer(first["printer"]).pos_profile, "one receipt printer per till")
			self.assertEqual(get_printer(second["printer"]).pos_profile, profile)
			till.set_till_printing(profile, receipt_printer=second["printer"], stations={"_Kitchen": second["printer"], "_Bar": second["printer"]})
			self.assertEqual(get_printer(second["printer"]).preparation_station, "_Bar, _Kitchen")
			till.set_till_printing(profile, receipt_printer=second["printer"], stations={"_Bar": None})
			self.assertEqual(get_printer(second["printer"]).preparation_station, "_Kitchen")
		finally:
			_remove_rows([first["printer"], second["printer"]])


class TestTillStations(FrappeTestCase):
	"""The stations a till lists: those of its restaurant, each by the name the printers keep and by its label."""

	STATIONS = [frappe._dict(name="h4sh0f4stn", station_name="Kitchen", station_type="Kitchen")]

	def listed(self, filters):
		"""get_till_printing(…)["stations"], the stations being those above, and what was asked of the database."""
		asked = {}
		real_get_all, real_exists = frappe.get_all, frappe.db.exists

		def get_all(doctype, *args, **kwargs):
			if doctype != "Preparation Station":
				return real_get_all(doctype, *args, **kwargs)
			asked.update(kwargs)
			return self.STATIONS

		def exists(doctype, *args, **kwargs):
			if doctype == "DocType" and args and args[0] == "Preparation Station":
				return True
			return real_exists(doctype, *args, **kwargs)

		with (
			patch.object(till, "_restaurant_filters", return_value=filters),
			patch.object(frappe, "get_all", get_all),
			patch.object(frappe.db, "exists", exists),
		):
			return till.get_till_printing(PROFILE)["stations"], asked

	def test_the_till_lists_its_stations_by_name_and_by_label(self):
		stations, _ = self.listed(None)
		self.assertEqual(
			stations,
			[{"station": "h4sh0f4stn", "station_name": "Kitchen", "station_type": "Kitchen", "printer": None}],
		)

	def test_the_till_lists_the_stations_of_its_restaurant_only(self):
		_, asked = self.listed([["restaurant", "=", "_Test Restaurant"]])
		self.assertEqual(asked["or_filters"], [["restaurant", "=", "_Test Restaurant"]])

	def test_the_restaurant_of_a_till_is_the_one_pos_next_says(self):
		scope = SimpleNamespace(
			current_restaurant=lambda profile: f"restaurant of {profile}",
			restaurant_or_filters=lambda restaurant: [["restaurant", "=", restaurant]],
		)
		restaurant_module = types.ModuleType("pos_next.restaurant")
		restaurant_module.scope = scope
		with patch.dict(sys.modules, {"pos_next": types.ModuleType("pos_next"), "pos_next.restaurant": restaurant_module}):
			self.assertEqual(till._restaurant_filters("Till 1"), [["restaurant", "=", "restaurant of Till 1"]])

	def test_without_pos_next_every_station_belongs_to_the_site(self):
		with patch.dict(sys.modules, {"pos_next.restaurant": None}):
			self.assertIsNone(till._restaurant_filters("Till 1"))


class TestKitchenSend(FrappeTestCase):
	def test_each_station_with_a_printer_gets_what_is_new_at_the_kitchen_once(self):
		pair = pair_printer(label="_Test CloudPRNT kitchen")
		invoice = "ACC-SINV-TEST-0003"

		def line(name, item, qty, station, status, batch=0):
			return frappe._dict(name=name, item_name=item, item_code=item.lower(), uom="Nos", qty=qty, preparation_station=station, kds_status=status, kds_batch=batch)

		order = SimpleNamespace(
			name=invoice,
			owner="Administrator",
			restaurant_table="Table 9",
			customer_name="Walk-in",
			get=lambda key, default=None: {"restaurant_table": "Table 9"}.get(key, default),
			items=[],
		)
		real_get_doc = frappe.get_doc

		def get_doc(*args, **kwargs):
			if args and args[0] == "Sales Invoice":
				return order
			return real_get_doc(*args, **kwargs)

		def tickets():
			return frappe.get_all(jobs.DOCTYPE, filters={"printer": pair["printer"]}, pluck="job_data", order_by="creation")

		try:
			update_printer(pair["printer"], {"preparation_station": "_Kitchen", "mac_address": "00:11:62:AA:00:31"})
			with patch.object(kitchen_ticket.frappe, "get_doc", side_effect=get_doc):
				# A soup sent with the order's first save carries no send number: it prints all the same.
				order.items = [
					line("row-1", "Soup", 1, "_Kitchen", "Pending"),
					line("row-2", "Beer", 2, "_Bar", "Pending"),
					line("row-3", "Cake", 1, "_Kitchen", "Waiting"),
				]
				kitchen_ticket.on_kitchen_send(invoice, 1)
				self.assertEqual(len(tickets()), 1, "the bar has no printer: its lines stay on its screen")
				self.assertIn("1 x Soup", tickets()[0])
				self.assertNotIn("Cake", tickets()[0], "a line still waiting is not at the kitchen")
				self.assertNotIn("Beer", tickets()[0])
				# A second soup, sent alone; then the till saves the order again under new line names, merged.
				order.items = [line("row-4", "Soup", 1, "_Kitchen", "Preparing"), line("row-5", "Soup", 1, "_Kitchen", "Pending")]
				kitchen_ticket.on_kitchen_send(invoice, 2)
				order.items = [line("row-6", "Soup", 2, "_Kitchen", "Pending"), line("row-7", "Cake", 1, "_Kitchen", "Waiting")]
				kitchen_ticket.on_kitchen_send(invoice, 2)
				# The cake goes later, as send 3.
				order.items[1] = line("row-8", "Cake", 1, "_Kitchen", "Pending", batch=3)
				kitchen_ticket.on_kitchen_send(invoice, 3)
			printed = tickets()
			self.assertEqual(len(printed), 3, "renamed or merged lines are not printed again")
			self.assertIn("1 x Soup", printed[1])
			self.assertIn("1 x Cake", printed[2])
			self.assertNotIn("Soup", printed[2])
			self.assertEqual(sorted(kitchen_ticket.printed_quantities(invoice).values()), [1, 2])
		finally:
			_remove_rows([pair["printer"]])


def _remove_rows(names):
	frappe.set_user("Administrator")
	settings = frappe.get_doc("CloudPRNT Settings")
	users = [row.device_user for row in settings.printers if row.name in names and row.device_user]
	settings.set("printers", [row for row in settings.printers if row.name not in names])
	if settings.default_printer in names:
		settings.default_printer = None
	settings.save()
	frappe.db.delete(jobs.DOCTYPE, {"printer": ("in", names)})
	for user in users:
		frappe.delete_doc("User", user, force=True, ignore_permissions=True)
