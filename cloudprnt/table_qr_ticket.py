"""The QR ticket of a table: a small paper to stick on the table or hand to the guests.

The till's QR button shows the code that opens the guest ordering page of a table; its Print button no longer opens the
browser's print window but asks POS Next, which names the moment (the hook `pos_next_table_qr_print`) and hands this app the
table and the link IT worked out from the guest token: this app prints what it is handed and never what a browser typed. The
paper holds the table's name in large letters, a line telling the guest to scan to order, the QR code of the link, and the
link in small characters under it, for a phone that cannot scan.

It prints on the receipt printer of the till that asks, like the provisional ticket, never opens the cash drawer, and expires
like a till receipt: a paper the printer did not take within RECEIPT_VALID_MINUTES is not printed by surprise later. The same
table and link asked for again within QR_WINDOW_SECONDS (a double tap) is the same paper: it prints once and the second
asker is told which job to follow (`ticket_status` of the provisional ticket follows any job of a till's printer).
"""

import hashlib
import json
from urllib.parse import quote, urlsplit

import frappe
from frappe import _
from frappe.utils import cint

from cloudprnt import print_queue_manager as jobs
from cloudprnt.api import _printer_for_profile
from cloudprnt.labels import table_label
from cloudprnt.receipt_markup import LINE_CHARS, _text, _width, printable

#: A table asked for again within this long is the same paper: a double tap prints once.
QR_WINDOW_SECONDS = 10
QR_CLAIM = "cloudprnt_table_qr:"
#: The longest link the ticket takes. The link of a table is a site address and a token: a longer one is not ours.
MAX_LINK_CHARS = 300
#: Every character a link may keep as it is; any other is written %XX, so that a link can neither end the tag it sits in
#: (`"`, `;`, `[`, `]`, `\`) nor carry a character that drives the printer.
URL_SAFE = "/:?#&=%+,~@-._"

#: The width of the paper in dots (a 203 dpi head), by the paper width in millimetres.
PRINT_DOTS = {58: 384, 80: 576, 112: 832}
#: The cell of the QR code, in dots: as big as the paper holds up to the 8 the printers take. A code of 37 modules (a link
#: of this app's length) drawn with cells of 8 dots is 3.7 cm wide, which a phone reads from a table.
MIN_CELL = 3
MAX_CELL = 8
#: Modules kept free on each side of the code (the printer's own margin is the white of the paper).
QUIET_MODULES = 2
#: Bytes a QR code holds at error correction M, by version (1 to 40): its capacity decides how many modules it has.
BYTES_AT_M = (
	14, 26, 42, 62, 84, 106, 122, 152, 180, 213, 251, 287, 331, 362, 412, 450, 504, 560, 624, 666,
	711, 779, 857, 911, 997, 1059, 1125, 1190, 1264, 1370, 1452, 1538, 1628, 1722, 1809, 1911, 1989, 2099, 2213, 2331,
)  # fmt: skip
#: The biggest the name of the table is magnified: wider than that and it is a poster, not a ticket.
MAX_NAME_MAGNIFY = 4


def default_paper_width():
	return frappe.db.get_single_value("CloudPRNT Settings", "default_paper_width")


def on_table_qr_print(table, link, pos_profile):
	"""Hook pos_next_table_qr_print: print the QR ticket of `table` (its record name) for the guest ordering `link`, on the
	receipt printer of till `pos_profile`. POS Next has checked who asks and worked the link out from the guest token.

	Answers {"printed", "printer", "printer_online", "job_token", "queue_position", "valid_minutes", "duplicate"}, or
	{"printed": False, "no_printer": True} when the till has no printer (the till then prints from its browser). The same
	table and link asked for again within QR_WINDOW_SECONDS prints nothing and answers the first one's job with duplicate
	true. A link that is not a web address is a refusal (frappe.ValidationError): nothing is queued.
	"""
	row = _printer_for_profile(pos_profile)
	if not row:
		return {"printed": False, "no_printer": True}
	link = _clean_link(link)
	cache = frappe.cache()
	claim = cache.make_key(claim_key(pos_profile, table, link))
	if not cache.set(claim, "pending", nx=True, ex=QR_WINDOW_SECONDS):
		return {**_remembered(cache.get(claim), row), "duplicate": True}
	try:
		answer = _print(row, table, link)
	except Exception:
		# Nothing came out: the next tap tries again
		cache.delete(claim)
		raise
	cache.set(claim, json.dumps(answer), ex=QR_WINDOW_SECONDS)
	return {**answer, "duplicate": False}


def claim_key(pos_profile, table, link):
	"""What a double tap is recognised by: the till, the table and the link (a new code for a table is a new paper)."""
	digest = hashlib.sha1(f"{pos_profile}\n{table}\n{link}".encode()).hexdigest()[:20]
	return f"{QR_CLAIM}{digest}"


def _print(row, table, link):
	paper = row.paper_width or default_paper_width()
	result = jobs.enqueue(
		row,
		get_table_qr_ticket_markup(table_label(table), link, paper_width=paper),
		# no open_drawer and no document: the paper is a ticket for the table, not a sale
		valid_seconds=jobs.RECEIPT_VALID_MINUTES * 60,
	)
	return {
		"printed": True,
		"printer": row.label,
		"printer_online": bool(cint(row.online)),
		"job_token": result.get("job_token"),
		"queue_position": result.get("queue_position"),
		"valid_minutes": jobs.RECEIPT_VALID_MINUTES,
	}


def _remembered(value, row):
	"""The answer the first tap left for the second; while the first is still queueing, only that it is on its way."""
	try:
		answer = json.loads(value)
	except (TypeError, ValueError):
		answer = None
	if isinstance(answer, dict):
		return answer
	return {
		"printed": True,
		"printer": row.label,
		"printer_online": bool(cint(row.online)),
		"job_token": None,
		"queue_position": None,
		"valid_minutes": jobs.RECEIPT_VALID_MINUTES,
	}


def _clean_link(link):
	"""The link as a web address of at most MAX_LINK_CHARS characters with no space or control character in it, else a refusal."""
	if not isinstance(link, str) or not link or len(link) > MAX_LINK_CHARS or printable(link) != link or " " in link:
		_refuse_link()
	parts = urlsplit(link)
	if parts.scheme not in ("http", "https") or not parts.netloc:
		_refuse_link()
	return link


def _refuse_link():
	frappe.throw(_("The ordering link of this table is not a web address"))


def _qr_data(link):
	"""The link as the QR tag and the small line under it carry it: every character outside URL_SAFE and the letters and
	digits written %XX."""
	return quote(link, safe=URL_SAFE)


def modules_of(data):
	"""How many modules wide the QR code of `data` is (a byte-mode code at error correction M): 17 + 4 per version."""
	size = len(data.encode("utf-8"))
	version = next((number for number, capacity in enumerate(BYTES_AT_M, start=1) if capacity >= size), len(BYTES_AT_M))
	return 17 + 4 * version


def qr_cell(data, paper_width=80):
	"""The cell, in dots, of the QR code of `data` on a paper `paper_width` mm wide: the biggest the paper holds, from
	MIN_CELL to MAX_CELL."""
	width = _width(paper_width)
	dots = PRINT_DOTS[min(PRINT_DOTS, key=lambda known: abs(known - width))]
	return max(MIN_CELL, min(MAX_CELL, dots // (modules_of(data) + 2 * QUIET_MODULES)))


def get_table_qr_ticket_markup(table_name, link, paper_width=80):
	"""Star Document Markup of the QR ticket of a table called `table_name` for the guest ordering `link`."""
	chars = LINE_CHARS.get(_width(paper_width), 48)
	data = _qr_data(link)
	name = printable(table_name)
	wide = max(1, min(MAX_NAME_MAGNIFY, chars // (len(name) or 1)))
	high = wide if wide > 1 else 2
	return "\n".join(
		[
			"[align: centre]",
			# The table's name, as large as the paper holds it on one line
			f"[magnify: width {wide}; height {high}][bold: on]{_text(name)}[bold: off][magnify]",
			"[feed: length 3mm]",
			f"[magnify: width 1; height 2]{_text(_('Scan to order'))}[magnify]",
			"[feed: length 5mm]",
			f'[barcode: type qr; data "{data}"; cell {qr_cell(data, paper_width)}; error-correction medium]',
			"[feed: length 5mm]",
			# The link in small characters (font B), for a phone that cannot scan
			"[font: b]",
			_text(data),
			"[font]",
			"[align: left]",
			"[cut: feed; partial]",
		]
	)
