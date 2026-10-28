"""What the till (POS Next) reads and sets about its printers: the receipt printer of a till, whether it prints
receipts by itself, when its cash drawer opens, and which printer each preparation station prints on.

The printer rows of the CloudPRNT settings stay the one place these live; the till's Printing settings read and
write them through here.
"""

import json

import frappe
from frappe.utils import cint

from cloudprnt import guards
from cloudprnt.printers import SETTINGS, all_printers, stations_of

DRAWER_RULES = ("Cash payments", "Always", "Never")
_PUBLIC = (
	"name",
	"label",
	"online",
	"status",
	"client_type",
	"paper_width",
	"drawer_connected",
	"paper_low",
	"paper_empty",
	"cover_open",
	"last_seen",
	"pos_profile",
	"preparation_station",
)


@frappe.whitelist()
def get_till_printing(pos_profile):
	"""The printers of the site and how till `pos_profile` uses them."""
	guards.require("Sales Invoice", "create")
	printers = all_printers()
	receipt = next((row for row in printers if pos_profile and row.pos_profile == pos_profile), None)
	stations = []
	if frappe.db.exists("DocType", "Preparation Station"):
		for station in frappe.get_all("Preparation Station", fields=["name", "station_type"], order_by="name"):
			holder = next((row.name for row in printers if station.name in stations_of(row)), None)
			stations.append({"station": station.name, "station_type": station.station_type, "printer": holder})
	return {
		"printers": [{key: row.get(key) for key in _PUBLIC} for row in printers],
		"receipt_printer": receipt.name if receipt else None,
		"auto_print": cint(receipt.auto_print) if receipt else 0,
		"open_drawer": (receipt.open_drawer or DRAWER_RULES[0]) if receipt else DRAWER_RULES[0],
		"stations": stations,
		"can_configure": bool(frappe.has_permission("CloudPRNT Settings", "write")),
	}


@frappe.whitelist()
def set_till_printing(pos_profile, receipt_printer=None, auto_print=None, open_drawer=None, stations=None):
	"""Set how till `pos_profile` prints: its receipt printer (None: none), whether receipts print by themselves
	when a sale is validated, when the drawer opens with a receipt, and the printer of each preparation station
	({station: printer row or None})."""
	guards.require("CloudPRNT Settings", "write")
	if isinstance(stations, str):
		stations = json.loads(stations or "{}")
	settings = frappe.get_doc(SETTINGS)
	for row in settings.printers:
		if row.pos_profile == pos_profile and row.name != receipt_printer:
			row.pos_profile = None
			row.auto_print = 0
		if receipt_printer and row.name == receipt_printer:
			row.pos_profile = pos_profile
			if auto_print is not None:
				row.auto_print = cint(auto_print)
			if open_drawer in DRAWER_RULES:
				row.open_drawer = open_drawer
	if stations is not None:
		for row in settings.printers:
			kept = [station for station in stations_of(row) if station not in stations]
			given = [station for station, printer in stations.items() if printer and printer == row.name]
			row.preparation_station = ", ".join(sorted(set(kept + given))) or None
	settings.save()
	return get_till_printing(pos_profile)
