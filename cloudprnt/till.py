"""What the till (POS Next) reads and sets about its printers: the receipt printer of a till, whether it prints
receipts by itself, when its cash drawer opens, and which printer each preparation station prints on.

The printer rows of the CloudPRNT settings stay the one place these live; the till's Printing settings read and
write them through here.
"""

import json

import frappe
from frappe.utils import cint

from cloudprnt import guards
from cloudprnt.kitchen_ticket import TEXT_SIZES, paper_only_stations, text_size_of, ticket_per_family_stations
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
	paper = paper_only_stations()
	per_family = ticket_per_family_stations()
	if frappe.db.exists("DocType", "Preparation Station"):
		# `station` is the name the printer rows keep, `station_name` what a person reads: POS Next names a new station
		# by a hash, and the same label can exist in two restaurants, so the till lists its own restaurant's only
		stations_of_the_till = frappe.get_all(
			"Preparation Station",
			fields=["name", "station_name", "station_type"],
			or_filters=_restaurant_filters(pos_profile),
			order_by="station_name, name",
		)
		for station in stations_of_the_till:
			holder = next((row.name for row in printers if station.name in stations_of(row)), None)
			stations.append(
				{
					"station": station.name,
					"station_name": station.station_name or station.name,
					"station_type": station.station_type,
					"printer": holder,
					# A station that works on paper alone: no screen, the ticket is all the kitchen reads
					"paper_only": 1 if station.name in paper else 0,
					# How big the writing of its kitchen tickets is: a key of TEXT_SIZES
					"text_size": text_size_of(station.name),
					# //// Neoffice — one ticket for each family of a send (« 1/3 STARTERS »), instead of one ticket with sections
					"ticket_per_family": 1 if station.name in per_family else 0,
				}
			)
	return {
		"printers": [{key: row.get(key) for key in _PUBLIC} for row in printers],
		"receipt_printer": receipt.name if receipt else None,
		"auto_print": cint(receipt.auto_print) if receipt else 0,
		"open_drawer": (receipt.open_drawer or DRAWER_RULES[0]) if receipt else DRAWER_RULES[0],
		"stations": stations,
		"can_configure": bool(frappe.has_permission("CloudPRNT Settings", "write")),
	}


def _restaurant_filters(pos_profile):
	"""The `or_filters` that keep the preparation stations of the restaurant a till belongs to; None when POS Next
	does not know restaurants (an older POS Next, or none): every station then belongs to the site."""
	try:
		from pos_next.restaurant import scope
	except ImportError:
		return None
	return scope.restaurant_or_filters(scope.current_restaurant(pos_profile))


@frappe.whitelist()
def set_till_printing(
	pos_profile,
	receipt_printer=None,
	auto_print=None,
	open_drawer=None,
	stations=None,
	paper_only=None,
	text_size=None,
	ticket_per_family=None,
):
	"""Set how till `pos_profile` prints: its receipt printer (None: none), whether receipts print by themselves
	when a sale is validated, when the drawer opens with a receipt, the printer of each preparation station
	({station: printer row or None}), which stations work on paper alone ({station: 0 or 1}: no screen, the
	ticket is all the kitchen reads; a station with no printer cannot be), and the size of the writing of each
	station's kitchen tickets ({station: Large, Medium or Small}), and which stations print a ticket for each
	family of a send ({station: 0 or 1})."""
	guards.require("CloudPRNT Settings", "write")
	if isinstance(stations, str):
		stations = json.loads(stations or "{}")
	if isinstance(paper_only, str):
		paper_only = json.loads(paper_only or "{}")
	if isinstance(text_size, str):
		text_size = json.loads(text_size or "{}")
	if isinstance(ticket_per_family, str):
		ticket_per_family = json.loads(ticket_per_family or "{}")
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
	if stations is not None or paper_only is not None:
		_write_paper_only(paper_flags(settings.printers, paper_only or {}, paper_only_stations()))
	if text_size:
		_write_text_size(text_size)
	if ticket_per_family:
		_write_ticket_per_family(ticket_per_family)
	return get_till_printing(pos_profile)


def paper_flags(printers, requested, current):
	"""The paper-only flag each station ends with, for the stations that change: the ones asked for (`requested`, {station: 0 or 1}), and
	the ones that lost their printer (`current` is the set that is paper only now). A station no printer prints for cannot be paper only:
	its screen is all it has."""
	served = {station for row in printers for station in stations_of(row)}
	flags = {station: 1 if cint(flag) and station in served else 0 for station, flag in requested.items()}
	for station in current:
		if station not in served:
			flags[station] = 0
	return flags


def _write_paper_only(flags):
	"""Write the flags on the Preparation Station rows, when POS Next has the field."""
	if not flags or not frappe.db.exists("DocType", "Preparation Station"):
		return
	if not frappe.get_meta("Preparation Station").has_field("paper_only"):
		return
	for station, flag in flags.items():
		if frappe.db.exists("Preparation Station", station):
			frappe.db.set_value("Preparation Station", station, "paper_only", flag)


def _write_text_size(sizes):
	"""Write the text size on the Preparation Station rows, when POS Next has the field; a size nobody knows is left out."""
	if not frappe.db.exists("DocType", "Preparation Station"):
		return
	if not frappe.db.has_column("Preparation Station", "ticket_text_size"):
		return
	for station, size in sizes.items():
		if size in TEXT_SIZES and frappe.db.exists("Preparation Station", station):
			frappe.db.set_value("Preparation Station", station, "ticket_text_size", size)


def _write_ticket_per_family(flags):
	"""Write the « a ticket per family » flag on the Preparation Station rows, when POS Next has the field."""
	if not frappe.db.exists("DocType", "Preparation Station"):
		return
	if not frappe.db.has_column("Preparation Station", "ticket_per_family"):
		return
	for station, flag in flags.items():
		if frappe.db.exists("Preparation Station", station):
			frappe.db.set_value("Preparation Station", station, "ticket_per_family", 1 if cint(flag) else 0)
