"""The kitchen ticket of a table order, printed at the preparation station that makes it.

A table order in the POS is a draft Sales Invoice: each line names its preparation station (Kitchen, Bar…), its
state at the kitchen and the send it went with (POS Next fields `preparation_station`, `kds_status`, `kds_batch`).
A ticket holds the lines of one station: what the station has to make now. No prices; the table, the quantities
and the remarks are what the cook reads first.

When lines go to the kitchen, each station with a printer gets one ticket of its lines sent and not printed yet.
The queue remembers the lines each ticket printed (CloudPRNT Print Queue.kitchen_rows): a line is printed once,
however often the order is saved afterwards, and whether or not the till numbered its send.

Unlike a till receipt, a kitchen ticket never expires: an order waits for its printer for as long as it takes.
"""

import json

import frappe
from frappe import _
from frappe.utils import flt, format_datetime, get_datetime

from cloudprnt import guards
from cloudprnt import print_queue_manager as jobs
from cloudprnt.printers import all_printers, find_printer, stations_of

LINE_CHARS = {58: 32, 80: 48, 112: 69}
#: The state of a line just sent to the kitchen, not started yet (POS Next kds_status).
SENT = "Pending"


@frappe.whitelist()
def print_kitchen_ticket(invoice, station=None, batch=None, printer=None):
	"""Print the kitchen ticket of table order `invoice`: the lines of `station` (each station on its own printer
	when none is given), of send `batch` when given. The printer is `printer`, else the one set for the station."""
	guards.require("Sales Invoice", "read")
	if not frappe.db.exists("Sales Invoice", invoice):
		frappe.throw(_("{0} {1} not found").format(_("Sales Invoice"), invoice), frappe.DoesNotExistError)
	guards.require("Sales Invoice", "read", invoice)
	doc = frappe.get_doc("Sales Invoice", invoice)
	stations = [station] if station else sorted({line.get("preparation_station") or "" for line in doc.items})
	queued = []
	for name in stations:
		lines = _lines(doc, name, batch)
		if not lines:
			continue
		row = find_printer(printer) if printer else _printer_for_station(name)
		if not row:
			frappe.throw(_("No CloudPRNT printer prints for the station {0}").format(name or _("(none)")))
		markup = get_kitchen_ticket_markup(doc, name, lines, batch, paper_width=row.paper_width)
		result = jobs.enqueue(row, markup, reference_doctype="Sales Invoice", reference_name=doc.name)
		queued.append({"station": name, "printer": row.label, **result})
	if not queued:
		frappe.throw(_("Nothing to send to the kitchen for this order"))
	return {"success": True, "message": _("Kitchen ticket sent"), "tickets": queued}


def on_kitchen_send(invoice, batch=None):
	"""Hook pos_next_kitchen_send: lines of table order `invoice` were just put at the kitchen (as send `batch`
	when the till numbered it). Each station with a printer gets one ticket of its sent lines not printed yet; a
	station without one keeps its screen only."""
	doc = frappe.get_doc("Sales Invoice", invoice)
	printed = printed_lines(doc.name)
	waiting = [
		line
		for line in doc.items
		if line.get("preparation_station") and line.get("kds_status") == SENT and line.name not in printed
	]
	for station in sorted({line.preparation_station for line in waiting}):
		row = _printer_for_station(station)
		if not row:
			continue
		lines = [line for line in waiting if line.preparation_station == station]
		send = max(int(line.get("kds_batch") or 0) for line in lines) or None
		jobs.enqueue(
			row,
			get_kitchen_ticket_markup(doc, station, lines, send, paper_width=row.paper_width),
			reference_doctype="Sales Invoice",
			reference_name=doc.name,
			kitchen_rows=[line.name for line in lines],
		)


def printed_lines(invoice):
	"""The lines of table order `invoice` a kitchen ticket already printed."""
	lists = frappe.get_all(
		jobs.DOCTYPE,
		filters={"reference_doctype": "Sales Invoice", "reference_name": invoice, "kitchen_rows": ("is", "set")},
		pluck="kitchen_rows",
	)
	return {name.strip() for names in lists for name in names.split(",") if name.strip()}


def get_kitchen_ticket_markup(doc, station, lines, batch=None, paper_width=80, title=None):
	"""Star Document Markup of one station's ticket for `lines` of table order `doc`."""
	chars = LINE_CHARS.get(_width(paper_width), 48)
	where = _where(doc)
	waiter = frappe.db.get_value("User", doc.owner, "first_name") or doc.owner
	sent = format_datetime(get_datetime(), "HH:mm")
	out = ["[align: centre]"]
	if title:
		out.append(f"[magnify: width 2; height 2][bold: on]{_text(title)}[bold: off][magnify]")
	out.append(f"[magnify: width 3; height 3][bold: on]{_text(where)}[bold: off][magnify]")
	if station:
		out.append(f"[magnify: width 2; height 1]{_text(station).upper()}[magnify]")
	out += [
		"[align: left]",
		_column(f"{_('Waiter')}: {waiter}", sent),
		# "Send no. 2": the second time this table's order went to the kitchen (a bare "Send" reads as the verb)
		_column(doc.name, _("Send no. {0}").format(batch) if batch else ""),
		"-" * chars,
	]
	for line in lines:
		qty = flt(line.qty)
		qty_text = str(int(qty)) if qty == int(qty) else f"{qty:g}"
		out.append(f"[magnify: width 2; height 2][bold: on]{qty_text} x {_text(line.item_name or line.item_code)}[bold: off][magnify]")
		remarks = _modifiers(line.get("posa_item_modifiers")) + _plain(line.get("posa_special_instructions"))
		for remark in remarks:
			out.append(f"   [magnify: width 1; height 2]> {_text(remark)}[magnify]")
	out += ["-" * chars, "[cut: feed; partial]"]
	return "\n".join(out)


def _modifiers(value):
	"""A line's options as the cook reads them: POS Next keeps them as JSON, [{"group": "Cooking", "options":
	[{"name": "Medium", "price_adjustment": 0}]}], printed "Cooking: Medium"; plain text is printed as it is."""
	text = (value or "").strip()
	if not text.startswith(("[", "{")):
		return _plain(text)
	try:
		groups = json.loads(text)
	except ValueError:
		return _plain(text)
	remarks = []
	for group in groups if isinstance(groups, list) else [groups]:
		if not isinstance(group, dict):
			continue
		names = [str(o.get("name")).strip() for o in group.get("options") or [] if isinstance(o, dict) and o.get("name")]
		if not names:
			continue
		label = str(group.get("group") or "").strip()
		remarks.append(f"{label}: {', '.join(names)}" if label else ", ".join(names))
	return remarks


def _plain(value):
	return [part.strip() for part in (value or "").splitlines() if part.strip()]


def _lines(doc, station, batch):
	selected = []
	for line in doc.items:
		if (line.get("preparation_station") or "") != (station or ""):
			continue
		if batch not in (None, "") and int(line.get("kds_batch") or 0) != int(batch):
			continue
		selected.append(line)
	return selected


def _printer_for_station(station):
	for row in all_printers():
		if station and station in stations_of(row):
			return row
	return None


def _where(doc):
	if doc.get("restaurant_table"):
		return doc.restaurant_table
	if doc.get("is_takeaway"):
		return f"{_('Takeaway')} {doc.get('takeaway_number') or ''}".strip()
	return doc.customer_name or doc.name


def _width(paper_width):
	try:
		return round(float(str(paper_width or 80).replace("mm", "")))
	except ValueError:
		return 80


def _text(value):
	return str(value or "").replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]").replace("\n", " ")


def _column(left, right):
	def param(text):
		return str(text or "").replace(";", ",").replace("[", "(").replace("]", ")").replace("\n", " ")

	return f"[column: left {param(left)}; right {param(right)}]"
