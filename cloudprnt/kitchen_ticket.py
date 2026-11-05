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

A station set PAPER ONLY (POS Next field `paper_only` of the Preparation Station: no screen, nothing followed on the screens or at
the tables) is a kitchen whose cooks read the paper and talk to the room. Its ticket holds the WHOLE meal from the first send: the
courses the waiter holds back too, drawn in sections by family (Starters, Mains, Desserts), so that the cooks tick them off on the paper.
What is fired later prints nothing: it is already on the paper (the next send counts per dish what the tickets printed). A guest's line
that waits for the waiter's acceptance is not the waiter's held course and is not printed before it is accepted.

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
from cloudprnt.labels import station_label, table_label_of_order, ticket_reference
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
#: The service families of a line (POS Next `service_family`) in the order a meal serves them: the sections of a ticket that holds several.
FAMILY_ORDER = ("Drink", "Starter", "Main", "Dessert")
# The families a plate per person is counted for (the covers of a table against what is ordered); a drink is not
COURSES = ("Starter", "Main", "Dessert")
#: The text sizes of a kitchen ticket (POS Next field `ticket_text_size` of the preparation station), as the Star magnification (width, height) of
#: each part: the title (a cancellation, a copy), the table, the station, a dish and the options under it. Large is what the tickets were before the
#: setting existed; Medium, the default, keeps the height a cook reads from afar and halves the width, so that a long dish name stays on one line.
TEXT_SIZES = {
	"Large": {"title": (2, 2), "table": (3, 3), "station": (2, 1), "dish": (2, 2), "option": (1, 2)},
	"Medium": {"title": (2, 1), "table": (2, 2), "station": (2, 1), "dish": (1, 2), "option": (1, 1)},
	"Small": {"title": (2, 1), "table": (2, 1), "station": (1, 1), "dish": (1, 1), "option": (1, 1)},
}
DEFAULT_TEXT_SIZE = "Medium"
#: A copy asked for again within this long is the same copy: a double tap, or the kitchen's tap and the runner's together,
#: print once. The second asker is told which job to follow.
COPY_WINDOW_SECONDS = 10
COPY_CLAIM = "cloudprnt_kitchen_copy:"


# //// Neoffice — the number a send is told by (product owner, 05.11: the ticket of an addition said « Send no. 1 » while the kitchen's screen said « Send 2 »). POS Next stores the number of a send counting from
# //// zero: the first send has none, the second is 1, the third 2 (`kds_batch`), and the screen says « Send 2 » for the second. The ticket now says what the screen says; the first send, unnumbered, says nothing.
def _send_number(stored):
	stored = int(stored or 0)
	return stored + 1 if stored else None


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
		markup = get_kitchen_ticket_markup(doc, name, lines, _send_number(batch), paper_width=row.paper_width)
		result = jobs.enqueue(row, markup, reference_doctype="Sales Invoice", reference_name=doc.name)
		queued.append({"station": name, "station_name": station_label(name), "printer": row.label, **result})
	if not queued:
		frappe.throw(_("Nothing to send to the kitchen for this order"))
	return {"success": True, "message": _("Kitchen ticket sent"), "tickets": queued}


def on_kitchen_send(invoice, batch=None):
	"""Hook pos_next_kitchen_send: lines of table order `invoice` were just put at the kitchen (as send `batch`
	when the till numbered it), or the order was saved after a send. Each station with a printer gets one ticket of
	what is at the kitchen and not printed yet; a station without one keeps its screen only.

	//// Neoffice — a dish taken off the order, or a quantity lowered, after its ticket came out is printed again as a
	CANCELLATION (see `_cancel_what_was_taken_back`): a kitchen that reads paper only has no screen to see a line struck."""
	doc = frappe.get_doc("Sales Invoice", invoice)
	at_kitchen, first_line, present = {}, {}, {}
	paper = paper_only_stations()
	for line in doc.items:
		if line.get("preparation_station"):
			key = dish_key(line)
			# Whatever the line's state: a line that loses its state in a save is still on the order
			present[key] = present.get(key, 0) + flt(line.qty)
			if _is_on_the_paper(line, paper):
				at_kitchen[key] = at_kitchen.get(key, 0) + flt(line.qty)
				first_line.setdefault(key, line)
	printed = printed_quantities(doc.name)
	_cancel_what_was_taken_back(doc, printed, present)
	# The dishes this save ADDED to an order the kitchen already had (POS Next puts them in the request's flags; nothing when it does not know them)
	added = frappe.flags.get("kitchen_additions") or {}
	new = {key: qty - printed.get(key, 0) for key, qty in at_kitchen.items() if qty - printed.get(key, 0) > 0}
	per_family = ticket_per_family_stations()
	for station in sorted({first_line[key].preparation_station for key in new}):
		row = _printer_for_station(station)
		if not row:
			continue
		keys = [key for key in new if first_line[key].preparation_station == station]
		lines = [_with_qty(first_line[key], new[key]) for key in keys]
		key_of = {id(line): key for key, line in zip(keys, lines)}
		send = _send_number(max(int(line.get("kds_batch") or 0) for line in lines))
		# //// Neoffice — a station set « a ticket per family » prints one ticket for each family of the send (see `_tickets`)
		for title, status, ticket_lines in _tickets(station in per_family, lines):
			jobs.enqueue(
				row,
				get_kitchen_ticket_markup(
					doc,
					station,
					ticket_lines,
					send,
					paper_width=row.paper_width,
					title=title,
					status=status,
					# //// Neoffice — the dishes this save ADDED to an order the kitchen already had, told by POS Next in the request's flags (Daniel, 05.11)
					addition=any(line.get("item_code") in added for line in ticket_lines),
				),
				reference_doctype="Sales Invoice",
				reference_name=doc.name,
				kitchen_rows={key_of[id(line)]: new[key_of[id(line)]] for line in ticket_lines},
			)


def on_kitchen_void(invoice):
	"""Hook pos_next_kitchen_void: table order `invoice`, a draft that had lines at the kitchen, is being deleted. Every dish whose
	ticket came out comes out cancelled, on the printer of its station; what never came out is left alone."""
	doc = frappe.get_doc("Sales Invoice", invoice)
	_cancel_what_was_taken_back(doc, printed_quantities(doc.name), {})


def _cancel_what_was_taken_back(doc, printed, present):
	"""A cancellation ticket per station for the dishes whose ticket came out and that are no longer on the order (or in a lower
	quantity): `printed` is what the kitchen tickets of the order printed, `present` what the order holds now, per dish. It is written
	down as a negative quantity, so that the next send counts what the kitchen really holds and a dish put back prints again."""
	gone = {key: qty - present.get(key, 0) for key, qty in printed.items() if qty - present.get(key, 0) > 0}
	for station in sorted({key.split("|", 1)[0] for key in gone} - {""}):
		row = _printer_for_station(station)
		if not row:
			continue
		keys = sorted(key for key in gone if key.split("|", 1)[0] == station)
		lines = [_line_of_the_dish(key, gone[key]) for key in keys]
		title = _("CANCELLED {0}").format(format_datetime(get_datetime(), "HH:mm"))
		jobs.enqueue(
			row,
			get_kitchen_ticket_markup(doc, station, lines, paper_width=row.paper_width, title=title, cancelled=True),
			reference_doctype="Sales Invoice",
			reference_name=doc.name,
			kitchen_rows={key: -gone[key] for key in keys},
		)


def _line_of_the_dish(key, qty):
	"""A ticket line for a dish that is no longer on the order, read back from its key (station, item, unit, options, remarks)."""
	_station, item_code, _uom, modifiers, remarks = (key.split("|", 4) + [""] * 5)[:5]
	name = frappe.db.get_value("Item", item_code, "item_name") if item_code else None
	return frappe._dict(
		item_code=item_code,
		item_name=name or item_code,
		qty=qty,
		posa_item_modifiers=modifiers,
		posa_special_instructions=remarks,
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
		send = _send_number(max(int(line.get("kds_batch") or 0) for line in lines))
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
	paper = paper_only_stations()
	at_kitchen = [
		line for line in doc.items if _is_on_the_paper(line, paper) and (line.get("kds_status") or "") != DELIVERED
	]
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


def paper_only_stations():
	"""The preparation stations that work on paper alone (POS Next field `paper_only`): none when POS Next does not know the field."""
	if not frappe.db.exists("DocType", "Preparation Station"):
		return set()
	if not frappe.get_meta("Preparation Station").has_field("paper_only"):
		return set()
	return set(frappe.get_all("Preparation Station", filters={"paper_only": 1}, pluck="name"))


def _is_on_the_paper(line, paper):
	"""Whether a line is on its station's ticket: it left for the kitchen, or, at a paper-only station, it is a course the waiter holds back
	(Waiting, in no batch: a held line that has a batch is a guest's, which the waiter has not accepted yet)."""
	status = line.get("kds_status") or ""
	if status not in NOT_SENT:
		return True
	return status == "Waiting" and (line.get("preparation_station") or "") in paper and not cint(line.get("kds_batch"))


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


def get_kitchen_ticket_markup(
	doc, station, lines, batch=None, paper_width=80, title=None, cancelled=False, status=None, addition=False
):
	"""Star Document Markup of one station's ticket for `lines` of table order `doc`. `cancelled`: the lines are dishes taken back,
	their quantities read with a minus. `status`: a line under the title that says what to do with the ticket (« Wait for the next course »).
	`addition`: the lines were ADDED to an order the kitchen already had (NeoResto lot 1, Daniel, 05.11: « il faut imprimer le ticket avec l'ajout »):
	the ticket is headed « Addition », so that a cook does not take it for a second copy of the table's ticket."""
	chars = LINE_CHARS.get(_width(paper_width), 48)
	where = _where(doc)
	waiter = frappe.db.get_value("User", doc.owner, "first_name") or doc.owner
	sent = format_datetime(get_datetime(), "HH:mm")
	size = station_text_size(station)
	# //// Neoffice — the number of people seated at the table (NeoResto lot 1, product owner, 05.11.2026): the cook reads « 3 people » under
	# //// the table and, in each course's heading, how many plates the table has ordered against them: « MAINS 2/3 ». A ticket of a table
	# //// without a number is as it was.
	covers = 0 if cancelled else cint(doc.get("restaurant_covers"))
	counts = _course_counts(doc) if covers else {}
	out = ["[align: centre]"]
	if title:
		out.append(_sized(_text(title), size["title"], bold=True))
	if status:
		out.append(_sized(_text(status).upper(), size["title"], bold=True))
	# //// Neoffice — an ADDITION to a table the kitchen already has, headed in the largest type of the ticket (Daniel, 05.11)
	if addition and not cancelled:
		out.append(_sized(_text(f"*** {_('Addition').upper()} ***"), size["title"], bold=True))
	out.append(_sized(_text(where), size["table"], bold=True))
	if covers:
		out.append(_sized(_text(_("{0} people").format(covers)), size["station"], bold=True))
	if station:
		# The label of the station: a station POS Next created is named by a hash
		out.append(_sized(_text(station_label(station)).upper(), size["station"]))
	out += [
		"[align: left]",
		_column(f"{_('Waiter')}: {waiter}", sent),
		# "Send no. 2": the second time this table's order went to the kitchen (a bare "Send" reads as the verb)
		# The restaurant's ticket number (room-table-number), not the draft's invoice number: that one comes in an order nobody chose
		_column(ticket_reference(doc), _("Send no. {0}").format(batch) if batch else ""),
		"-" * chars,
	]
	for heading, section in _sections(lines):
		course = _course_of(section) if covers else None
		# The count stands in the heading of a course; a ticket of one course has no heading but for it, unless its title already names the course
		if course and (heading or not title):
			heading = f"{heading or _family_titles()[course]} {_number(counts.get(course, 0))}/{covers}"
		if heading:
			out.append(f"[bold: on]{_text(f' {heading.upper()} '.center(chars, '='))}[bold: off]")
		for line in section:
			qty = flt(line.qty)
			qty_text = str(int(qty)) if qty == int(qty) else f"{qty:g}"
			if cancelled:
				qty_text = f"-{qty_text}"
			out.append(_sized(f"{qty_text} x {_text(line.item_name or line.item_code)}", size["dish"], bold=True))
			remarks = _modifiers(line.get("posa_item_modifiers")) + _plain(line.get("posa_special_instructions"))
			for remark in remarks:
				out.append("   " + _sized(f"> {_text(remark)}", size["option"]))
	out += ["-" * chars, "[cut: feed; partial]"]
	return "\n".join(out)


def ticket_per_family_stations():
	"""The preparation stations whose tickets come out one per family (POS Next field `ticket_per_family`): none when POS Next does not know the field."""
	if not frappe.db.exists("DocType", "Preparation Station"):
		return set()
	if not frappe.db.has_column("Preparation Station", "ticket_per_family"):
		return set()
	return set(frappe.get_all("Preparation Station", filters={"ticket_per_family": 1}, pluck="name"))


def _tickets(per_family, lines):
	"""The tickets of one station's send, as [(title, status, lines)]: one with every family on it in sections, or, at a station set a ticket per
	family and when the send holds several, one for each family in the order of the meal, titled « 1/3 STARTERS ». Where some family is held back by
	the waiter (a paper-only station keeps the whole meal on paper), each ticket says what to do: « To do » for what leaves, « Wait for the next
	course » for what is held, so that a ticket goes out with its plates, when they go out."""
	sections = _sections(lines)
	if not per_family or len(sections) < 2:
		return [(None, None, lines)]
	held = [all((line.get("kds_status") or "") == "Waiting" for line in section) for _heading, section in sections]
	tickets = []
	for index, ((heading, section), is_held) in enumerate(zip(sections, held), start=1):
		status = (_("Wait for the next course") if is_held else _("To do")) if any(held) else None
		tickets.append((f"{index}/{len(sections)} {heading.upper()}", status, section))
	return tickets


def text_size_of(station):
	"""The name of the text size (a key of TEXT_SIZES) of the kitchen tickets of `station`: what the station sets (POS Next field
	`ticket_text_size`), else the default size, also when POS Next does not know the field yet."""
	size = None
	if station and frappe.db.exists("DocType", "Preparation Station") and frappe.db.has_column("Preparation Station", "ticket_text_size"):
		size = frappe.db.get_value("Preparation Station", station, "ticket_text_size")
	return size if size in TEXT_SIZES else DEFAULT_TEXT_SIZE


def station_text_size(station):
	"""The magnification of each part (TEXT_SIZES) of the kitchen tickets of `station`."""
	return TEXT_SIZES[text_size_of(station)]


def _sized(text, size, bold=False):
	"""`text` magnified to `size` (width, height), in bold when asked; the printer's normal size (1, 1) is no magnification at all."""
	body = f"[bold: on]{text}[bold: off]" if bold else text
	width, height = size
	return body if (width, height) == (1, 1) else f"[magnify: width {width}; height {height}]{body}[magnify]"


def _sections(lines):
	"""The lines of a ticket in sections by service family, as [(title, lines)], in the order of a meal. A ticket whose lines are all of one family
	(or of none) is one section with no title: a title that says nothing is noise. A line whose family nobody knows is of none, listed last."""
	by_family = {}
	for line in lines:
		family = (line.get("service_family") or "").strip()
		by_family.setdefault(family if family in FAMILY_ORDER else "", []).append(line)
	if len(by_family) < 2:
		return [("", list(lines))]
	titles = _family_titles()
	return [(titles[family], by_family[family]) for family in (*FAMILY_ORDER, "") if family in by_family]


def _family_titles():
	"""The heading of each family, in the language of the moment (never at import: the language is not known yet)."""
	return {
		"Drink": _("Drinks"),
		"Starter": _("Starters"),
		"Main": _("Mains"),
		"Dessert": _("Desserts"),
		"": _("Other"),
	}


def _course_counts(doc):
	"""How many plates the whole order holds per course, as {family: quantity}: the lines of every station and of every send, a drink
	or a line of no family counted for nothing - the covers of the table are the number each course is read against."""
	counts = {}
	for line in doc.items or []:
		family = (line.get("service_family") or "").strip()
		if family in COURSES:
			counts[family] = counts.get(family, 0) + flt(line.get("qty"))
	return counts


def _course_of(section):
	"""The course a section of a ticket holds, when all its lines are of the same one (None for a drink, a mix or lines of no family)."""
	families = {(line.get("service_family") or "").strip() for line in section}
	family = next(iter(families)) if len(families) == 1 else None
	return family if family in COURSES else None


def _number(value):
	return str(int(value)) if value == int(value) else f"{value:g}"


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
		# //// Neoffice — and, when the table holds several open orders, the rank of this one and its party's name: « T5.1 · Dupont » (NeoResto lot 1, notes)
		return table_label_of_order(doc)
	if doc.get("is_takeaway"):
		return f"{_('Takeaway')} {doc.get('takeaway_number') or ''}".strip()
	return doc.customer_name or doc.name


def _width(paper_width):
	try:
		return round(float(str(paper_width or 80).replace("mm", "")))
	except ValueError:
		return 80
