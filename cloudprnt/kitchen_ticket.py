"""The kitchen ticket of a table order, printed at the preparation station that makes it.

A table order in the POS is a draft Sales Invoice: each line names its preparation station (Kitchen, Bar…), its
state at the kitchen and the send it went with (POS Next fields `preparation_station`, `kds_status`, `kds_batch`).
A ticket holds the lines of one station: what the station has to make now. No prices; the table, the quantities
and the remarks are what the cook reads first.

When lines go to the kitchen, each station with a printer gets one ticket of what is at the kitchen and not
printed yet, counted per dish (station, item, unit, options, remarks): the till rewrites an order's lines under new
names at every save, so a line's name cannot say whether it was printed. The queue keeps the quantities each ticket
printed (CloudPRNT Print Queue.kitchen_rows); a ticket prints the difference, however often the order is saved and
whether or not the till numbered its send.

Unlike a till receipt, a kitchen ticket never expires: an order waits for its printer for as long as it takes.

A COPY is the same paper asked for again from the kitchen's or the runner's screen (POS Next names the moment, the hook
`pos_next_kitchen_reprint`): a big order is carried to the pass on paper. It holds the lines of one station that are at the
kitchen (or the whole order), marked COPY and the time so that nobody cooks twice. It is not a send: it never writes down
what it printed (`kitchen_rows`), so the next send prints exactly what it would have printed without it. It expires like a
till receipt, since a copy that comes out an hour late, when the printer is back, is a ticket to cook twice.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, format_datetime, get_datetime

from cloudprnt import guards
from cloudprnt import print_queue_manager as jobs
from cloudprnt.labels import station_label, table_label, ticket_reference
from cloudprnt.printers import all_printers, find_printer, get_printer, stations_of

# The kitchen's text goes through the same helpers as the receipt's: one function makes it safe for a printer (a guest's
# remark is stored as typed, and BEL or ESC @ in it must not reach the printer), and its columns keep a backslash or a
# ';' from breaking the line.
from cloudprnt.receipt_markup import _column, _text

LINE_CHARS = {58: 32, 80: 48, 112: 69}
#: The states of a line not sent to the kitchen yet (POS Next kds_status); any other state is at the kitchen.
NOT_SENT = ("", "Waiting")
#: The state of a dish that is done with: nothing left to cook, so nothing on a copy.
DELIVERED = "Delivered"
#: A copy asked for again within this long is the same copy: a double tap, or the kitchen's tap and the runner's together,
#: print once. The second asker is told which job to follow.
COPY_WINDOW_SECONDS = 10
COPY_CLAIM = "cloudprnt_kitchen_copy:"


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
			frappe.throw(_("No CloudPRNT printer prints for the station {0}").format(station_label(name) or _("(none)")))
		markup = get_kitchen_ticket_markup(doc, name, lines, batch, paper_width=row.paper_width)
		result = jobs.enqueue(row, markup, reference_doctype="Sales Invoice", reference_name=doc.name)
		queued.append({"station": name, "station_name": station_label(name), "printer": row.label, **result})
	if not queued:
		frappe.throw(_("Nothing to send to the kitchen for this order"))
	return {"success": True, "message": _("Kitchen ticket sent"), "tickets": queued}


def on_kitchen_send(invoice, batch=None):
	"""Hook pos_next_kitchen_send: lines of table order `invoice` were just put at the kitchen (as send `batch`
	when the till numbered it). Each station with a printer gets one ticket of what is at the kitchen and not
	printed yet; a station without one keeps its screen only."""
	doc = frappe.get_doc("Sales Invoice", invoice)
	at_kitchen, first_line = {}, {}
	for line in doc.items:
		if line.get("preparation_station") and (line.get("kds_status") or "") not in NOT_SENT:
			key = dish_key(line)
			at_kitchen[key] = at_kitchen.get(key, 0) + flt(line.qty)
			first_line.setdefault(key, line)
	printed = printed_quantities(doc.name)
	new = {key: qty - printed.get(key, 0) for key, qty in at_kitchen.items() if qty - printed.get(key, 0) > 0}
	for station in sorted({first_line[key].preparation_station for key in new}):
		row = _printer_for_station(station)
		if not row:
			continue
		keys = [key for key in new if first_line[key].preparation_station == station]
		lines = [_with_qty(first_line[key], new[key]) for key in keys]
		send = max(int(line.get("kds_batch") or 0) for line in lines) or None
		jobs.enqueue(
			row,
			get_kitchen_ticket_markup(doc, station, lines, send, paper_width=row.paper_width),
			reference_doctype="Sales Invoice",
			reference_name=doc.name,
			kitchen_rows={key: new[key] for key in keys},
		)


def on_kitchen_reprint(invoice, station=None, whole_order=False, mark_copy=True):
	"""Hook pos_next_kitchen_reprint: print a COPY of the ticket of table order `invoice` for the screen that asked.

	A station given: its printer prints the lines of that station that are at the kitchen, or the whole order when
	`whole_order` (every line at the kitchen, whatever its station). No station (the screen shows them all): each station
	with a printer prints its own lines, or each printer prints the whole order once. `mark_copy` puts "COPY hh:mm" at the
	head of the paper. Lines not sent yet (a course held back) and lines delivered are never on a copy.

	Answers {"tickets": [{station, station_name, printer, printer_online, job_token, queue_position}], "no_printer":
	[stations at the kitchen with no printer], "valid_minutes", "duplicate"}: with no ticket and a station in `no_printer`,
	the screen hides its button. The same copy asked for again within COPY_WINDOW_SECONDS prints nothing and answers the
	first one's tickets with duplicate true. A copy writes down nothing the next send reads (no kitchen_rows).
	"""
	whole_order = bool(cint(whole_order))
	mark_copy = mark_copy not in (0, "0", False, "false", "False")
	claim = frappe.cache().make_key(f"{COPY_CLAIM}{invoice}:{station or '*'}:{'order' if whole_order else 'station'}")
	cache = frappe.cache()
	if not cache.set(claim, "pending", nx=True, ex=COPY_WINDOW_SECONDS):
		return {**_remembered(cache.get(claim)), "duplicate": True}
	try:
		answer = _print_copy(frappe.get_doc("Sales Invoice", invoice), station, whole_order, mark_copy)
	except Exception:
		# Nothing came out: the next tap tries again
		cache.delete(claim)
		raise
	cache.set(claim, json.dumps(answer), ex=COPY_WINDOW_SECONDS)
	return {**answer, "duplicate": False}


def _remembered(value):
	"""The answer a first tap left for the second (nothing yet when the first is still printing)."""
	try:
		answer = json.loads(value)
	except (TypeError, ValueError):
		answer = None
	return answer if isinstance(answer, dict) else {"tickets": [], "no_printer": [], "valid_minutes": jobs.RECEIPT_VALID_MINUTES}


def _print_copy(doc, station, whole_order, mark_copy):
	targets, no_printer = _copy_targets(doc, station, whole_order)
	title = _("COPY {0}").format(format_datetime(get_datetime(), "HH:mm")) if mark_copy else None
	tickets = []
	for name, row, lines in targets:
		send = max(int(line.get("kds_batch") or 0) for line in lines) or None
		# The header names the station whose lines these are; a whole order belongs to no station
		markup = get_kitchen_ticket_markup(
			doc, "" if whole_order else name, lines, send, paper_width=row.paper_width, title=title
		)
		result = jobs.enqueue(
			row,
			markup,
			reference_doctype="Sales Invoice",
			reference_name=doc.name,
			valid_seconds=jobs.RECEIPT_VALID_MINUTES * 60,
		)
		tickets.append(
			{
				"station": name,
				"station_name": station_label(name),
				"printer": row.label,
				"printer_online": bool(cint(row.online)),
				"job_token": result.get("job_token"),
				"queue_position": result.get("queue_position"),
			}
		)
	return {"tickets": tickets, "no_printer": no_printer, "valid_minutes": jobs.RECEIPT_VALID_MINUTES}


def _copy_targets(doc, station, whole_order):
	"""What a copy prints and where: [(station, printer row, lines)], and the stations at the kitchen that have no printer."""
	at_kitchen = [line for line in doc.items if (line.get("kds_status") or "") not in (*NOT_SENT, DELIVERED)]
	stations = [station] if station else sorted({line.get("preparation_station") or "" for line in at_kitchen} - {""})
	targets, no_printer, done = [], [], set()
	for name in stations:
		row = _printer_for_station(name)
		if not row:
			no_printer.append(name)
			continue
		if whole_order:
			# One printer serves several stations: it prints the order once
			if row.name in done:
				continue
			done.add(row.name)
			lines = at_kitchen
		else:
			lines = [line for line in at_kitchen if (line.get("preparation_station") or "") == name]
		if lines:
			targets.append((name, row, lines))
	return targets, no_printer


def kitchen_copy_status(job, invoice):
	"""Hook pos_next_kitchen_reprint_status: where the copy `job` of table order `invoice` stands (Pending, Fetched, Printed,
	Failed or Expired), whether its printer is on, and why it did not print; None when it is no job of this app's kitchen
	copies (a till's receipt is the till's business). POS Next has checked that the caller may follow this order."""
	if not isinstance(job, str) or not job or not isinstance(invoice, str) or not invoice:
		return None
	row = frappe.db.get_value(
		jobs.DOCTYPE,
		{"job_token": job, "reference_doctype": "Sales Invoice", "reference_name": invoice},
		["status", "printer", "result_code"],
		as_dict=True,
	)
	printer = get_printer(row.printer) if row and row.printer else None
	if not row or not printer or not stations_of(printer):
		return None
	return {
		"status": row.status,
		"printer_online": bool(cint(printer.online)),
		"reason": row.result_code if row.status in ("Failed", "Expired") else "",
	}


def stations_with_a_printer():
	"""Hook pos_next_kitchen_printers: the preparation stations some printer prints for. A screen shows the button of a copy
	only for these."""
	return sorted({station for row in all_printers() for station in stations_of(row)})


def dish_key(line):
	"""What a kitchen line is, whatever the till named it: station, item, unit, options and remarks."""
	return "|".join(
		[
			line.get("preparation_station") or "",
			line.get("item_code") or "",
			line.get("uom") or "",
			"; ".join(_modifiers(line.get("posa_item_modifiers"))),
			"; ".join(_plain(line.get("posa_special_instructions"))),
		]
	)


def printed_quantities(invoice):
	"""What the kitchen tickets of table order `invoice` printed: quantity per dish (dish_key)."""
	printed = {}
	for value in frappe.get_all(
		jobs.DOCTYPE,
		filters={"reference_doctype": "Sales Invoice", "reference_name": invoice, "kitchen_rows": ("is", "set")},
		pluck="kitchen_rows",
	):
		try:
			quantities = json.loads(value)
		except ValueError:
			continue
		for key, qty in quantities.items() if isinstance(quantities, dict) else []:
			printed[key] = printed.get(key, 0) + flt(qty)
	return printed


def _with_qty(line, qty):
	# A frappe._dict answers every attribute (as_dict among them): ask whether it is a dict, not what it has.
	copy = frappe._dict(line if isinstance(line, dict) else line.as_dict())
	copy.qty = qty
	return copy


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
		# The label of the station: a station POS Next created is named by a hash
		out.append(f"[magnify: width 2; height 1]{_text(station_label(station)).upper()}[magnify]")
	out += [
		"[align: left]",
		_column(f"{_('Waiter')}: {waiter}", sent),
		# "Send no. 2": the second time this table's order went to the kitchen (a bare "Send" reads as the verb)
		# The restaurant's ticket number (room-table-number), not the draft's invoice number: that one comes in an order nobody chose
		_column(ticket_reference(doc), _("Send no. {0}").format(batch) if batch else ""),
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
		# The label of the table: a table POS Next created is named by a hash
		return table_label(doc.restaurant_table)
	if doc.get("is_takeaway"):
		return f"{_('Takeaway')} {doc.get('takeaway_number') or ''}".strip()
	return doc.customer_name or doc.name


def _width(paper_width):
	try:
		return round(float(str(paper_width or 80).replace("mm", "")))
	except ValueError:
		return 80
