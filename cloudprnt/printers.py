"""The printers configured on the site: the rows of the CloudPRNT Settings' printer table.

A row is found by its name, its label or its MAC address, whichever a caller holds (the POS and the payments
app pass a label or a MAC, the settings form passes the row name).
"""

import re

import frappe
from frappe import _

SETTINGS = "CloudPRNT Settings"
PRINTER = "CloudPRNT Printers"

#: The fields the server writes on a row (identity of the device, what it reported, its last status). The
#: settings form carries them read-only and saves them back as it loaded them: CloudPRNTSettings.validate
#: puts the stored values back, so a form opened before a printer reported does not wipe what it reported.
SERVER_FIELDS = (
	"mac_address",
	"device_user",
	"client_type",
	"client_version",
	"media_types",
	"paper_width",
	"print_dots",
	"ip_address",
	"last_seen",
	"online",
	"status",
	"status_code",
	"printing_in_progress",
	"cover_open",
	"paper_empty",
	"paper_low",
	"drawer_connected",
	"drawer_open",
	"compulsion_switch",
	"cutter_error",
	"mechanical_error",
	"over_temperature",
	"presenter_paper_jam",
	"receive_buffer_overflow",
	"black_mark_error",
	"voltage_error",
	"recoverable",
)

_MAC = re.compile(r"^[0-9A-F]{12}$")


def normalize_mac(mac):
	"""A MAC address as XX:XX:XX:XX:XX:XX, whatever separators it came with (CloudPRNT sends dots or colons,
	in either case), or None when it is not one."""
	digits = re.sub(r"[^0-9A-Fa-f]", "", str(mac or "")).upper()
	if not _MAC.match(digits):
		return None
	return ":".join(digits[i : i + 2] for i in range(0, 12, 2))


def all_printers():
	return frappe.get_all(PRINTER, filters={"parent": SETTINGS, "parenttype": SETTINGS}, fields=["*"], order_by="idx")


def get_printer(name):
	rows = frappe.get_all(PRINTER, filters={"parent": SETTINGS, "name": name}, fields=["*"])
	return rows[0] if rows else None


def find_printer(printer=None):
	"""The row named by `printer` (row name, label or MAC address), or the default printer when none is given.

	Raises a translated error when there is no such printer.
	"""
	printer = (printer or "").strip() or frappe.db.get_single_value(SETTINGS, "default_printer")
	if not printer:
		frappe.throw(_("No CloudPRNT printer given and no default printer configured"))
	for row in all_printers():
		if printer in (row.name, row.label):
			return row
	mac = normalize_mac(printer)
	if mac:
		for row in all_printers():
			if normalize_mac(row.mac_address) == mac:
				return row
	frappe.throw(_("CloudPRNT printer {0} not found").format(printer))


def printer_for_mac(mac):
	mac = normalize_mac(mac)
	if not mac:
		return None
	for row in all_printers():
		if normalize_mac(row.mac_address) == mac:
			return row
	return None


def printer_for_user(user):
	if not user or user == "Guest":
		return None
	rows = frappe.get_all(PRINTER, filters={"parent": SETTINGS, "device_user": user}, fields=["*"])
	return rows[0] if rows else None


def stations_of(row):
	"""The preparation stations a printer row prints for (one printer may serve the kitchen and the bar)."""
	return [part.strip() for part in (row.get("preparation_station") or "").split(",") if part.strip()]


def update_printer(name, values):
	"""Write server-owned fields on a row without touching the settings' modified time."""
	if values:
		frappe.db.set_value(PRINTER, name, values, update_modified=False)


#: A printer that has not polled for this long is shown offline.
SILENT_AFTER_SECONDS = 120


def mark_silent_printers_offline():
	"""Scheduled: a printer polls every few seconds while it is on and connected; one silent for two minutes is
	off, unplugged or cut from the network."""
	from frappe.utils import add_to_date, now_datetime

	cutoff = add_to_date(now_datetime(), seconds=-SILENT_AFTER_SECONDS)
	for row in all_printers():
		if row.online and (not row.last_seen or row.last_seen < cutoff):
			update_printer(row.name, {"online": 0, "status": _("Offline")})
