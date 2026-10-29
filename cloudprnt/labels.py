"""What a person reads of a POS Next record on a ticket or a screen: its label, not its name.

POS Next names a new room, table or preparation station by a hash and keeps what a person reads in a label field
(`table_name`, `station_name`). An older record is named by its label, and a site without POS Next has no such record
at all: the name is then all there is to read. Printing the name would put "k3x9q2m7ab" on the cook's ticket.
"""

import frappe


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
