"""What a person reads of a POS Next record on a ticket or a screen: its label, not its name.

POS Next names a new room, table or preparation station by a hash and keeps what a person reads in a label field
(`table_name`, `station_name`). An older record is named by its label, and a site without POS Next has no such record
at all: the name is then all there is to read. Printing the name would put "k3x9q2m7ab" on the cook's ticket.
"""

import frappe
from frappe.utils import cint

# The code a takeaway order stands for where a room's code would be (POS Next: pos_next.restaurant.room_code.TAKEAWAY_CODE)
TAKEAWAY_CODE = "EMP"
# The digits a ticket number is written with when the restaurant has not said (POS Next: Restaurant.ticket_number_digits)
DEFAULT_TICKET_DIGITS = 4


def label_of(doctype, label_field, name):
	"""The label of record `name` of `doctype`, else its name (also when POS Next, or its label field, is absent)."""
	if not name:
		return name or ""
	if frappe.db.exists("DocType", doctype) and frappe.db.has_column(doctype, label_field):
		return frappe.db.get_value(doctype, name, label_field) or name
	return name


def table_label(name):
	return label_of("Restaurant Table", "table_name", name)


def station_label(name):
	return label_of("Preparation Station", "station_name", name)


# //// Neoffice — the notes of a table (NeoResto lot 1, notes): a table may hold several open orders, one per party, told ".1", ".2" with the party's name beside the rank. The rule that
# //// composes the label lives in POS Next (pos_next.restaurant.notes.table_label); a site without it, or not migrated yet, reads the table's label alone, as it always did.
def table_label_of_order(doc):
	"""The label of the table of order `doc` as its ticket names it: « T5 » while the table holds one open order, « T5.1 » or « T5.1 · Dupont » when it holds several (the first is « T5.0 »).

	The order may be saved (a kitchen ticket) or not (the provisional ticket, built from the till's payload, which carries the rank and the name of its note); the open orders
	of the table other than `doc` itself are counted, and `doc` is the one more. `doc.flags.note_draft` names the saved draft an unsaved order stands for, so that it does not count twice."""
	table = doc.get("restaurant_table")
	label = table_label(table)
	if not table:
		return label
	try:
		from pos_next.restaurant import notes
	except ImportError:
		return label
	if not notes.has_columns():
		return label
	flags = getattr(doc, "flags", None) or {}
	own = flags.get("note_draft") or doc.get("name") or ""
	others = frappe.db.count("Sales Invoice", {"docstatus": 0, "restaurant_table": table, "name": ["!=", own]})
	return notes.table_label(label, doc.get("restaurant_note"), doc.get("restaurant_note_name"), others + 1)


def room_and_digits_of(doc):
	"""The code of the room an order sits in and the digits its restaurant writes its ticket numbers with, as (code, digits).

	Read from POS Next's records, where the site has them: the room of the order's table (the code typed on it, else the one
	POS Next makes from its name), EMP for a takeaway order, no room ("") for anything else; four digits when the restaurant
	says none. A site without POS Next, or not migrated yet, answers ("", 4).
	"""
	table = doc.get("restaurant_table")
	room, restaurant = "", None
	if table and frappe.db.exists("DocType", "Restaurant Table"):
		row = frappe.db.get_value("Restaurant Table", table, ["area", "restaurant"], as_dict=True) or {}
		restaurant = row.get("restaurant")
		room = _code_of_room(row.get("area"))
	elif not table and cint(doc.get("is_takeaway")):
		room = TAKEAWAY_CODE
		# A takeaway order sits at no table: its restaurant is the one of the till that took it
		restaurant = _restaurant_of_till(doc.get("pos_profile"))
	digits = DEFAULT_TICKET_DIGITS
	if (
		restaurant
		and frappe.db.exists("DocType", "Restaurant")
		and frappe.db.has_column("Restaurant", "ticket_number_digits")
	):
		digits = cint(frappe.db.get_value("Restaurant", restaurant, "ticket_number_digits"))
		digits = digits if 1 <= digits <= 9 else DEFAULT_TICKET_DIGITS
	return room, digits


def _restaurant_of_till(pos_profile):
	"""The restaurant a till works for (the one its enabled POS Settings name), None when POS Next cannot say."""
	if not pos_profile or not frappe.db.exists("DocType", "POS Settings"):
		return None
	if not frappe.db.has_column("POS Settings", "restaurant"):
		return None
	return frappe.db.get_value("POS Settings", {"pos_profile": pos_profile, "enabled": 1}, "restaurant") or None


def _code_of_room(area):
	"""The code of a room: typed on it, else made from its name by POS Next's own rule (empty when POS Next cannot say)."""
	if not area or not frappe.db.exists("DocType", "Restaurant Area"):
		return ""
	fields = ["area_name"] + (["short_code"] if frappe.db.has_column("Restaurant Area", "short_code") else [])
	row = frappe.db.get_value("Restaurant Area", area, fields, as_dict=True) or {}
	typed = (row.get("short_code") or "").strip().upper()
	try:
		from pos_next.restaurant.room_code import room_code
	except ImportError:
		# A POS Next older than the room codes: only a code typed on the room can be told
		return typed
	return room_code(typed, row.get("area_name"))


def ticket_reference(doc):
	"""What the kitchen paper reads for an order besides the table: its TICKET number, written `room-table-number` (S01-Table
	15-0007; a takeaway order EMP-0012).

	A table order is a draft Sales Invoice, and a draft takes its invoice number at its first save, in an order nobody chose and with
	a gap for each draft deleted: that is no number to give a cook. The restaurant counts its own tickets (POS Next,
	`posa_ticket_no`, per restaurant and business day); an order that has none (one saved before they existed, or a site without
	POS Next) keeps its document name.
	"""
	number = cint(doc.get("posa_ticket_no"))
	if not number:
		return doc.name
	room, digits = room_and_digits_of(doc)
	table = doc.get("restaurant_table")
	parts = [room, table_label(table) if table else "", str(number).zfill(digits)]
	return "-".join(part for part in parts if part)
