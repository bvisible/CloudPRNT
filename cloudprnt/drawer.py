"""The cash drawer, opened from the till, and the journal of its openings (Cash Drawer Opening).

Every opening is written down: with a cash sale (reason Sale, the sale linked), or alone, by a cashier whose
till session is open, with a reason (and a note for Other). The printer's answer completes the line: Opened
when it printed the job with the drawer pulse, Not opened when the job failed, expired, or came too late for the
drawer. The journal is kept; the print queue behind it is not.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

from cloudprnt import guards
from cloudprnt import print_queue_manager as jobs
from cloudprnt.printers import all_printers

DOCTYPE = "Cash Drawer Opening"
SALE = "Sale"
#: The reasons a cashier picks from to open the drawer without a sale.
REASONS = ("Give change", "Cash float", "Change error", "Other")
OPENED = "Opened"
NOT_OPENED = "Not opened"
SENT = "Sent"


@frappe.whitelist()
def open_from_till(pos_profile, reason, note=None, pos_opening_shift=None):
	"""Open the cash drawer of till `pos_profile` without a sale, for `reason` (one of REASONS; Other needs a
	note). Any cashier whose till session on that profile is open may; the opening is journaled to them."""
	guards.require("Sales Invoice", "create")
	if reason not in REASONS:
		frappe.throw(_("Choose why the drawer is opened"))
	note = (note or "").strip()
	if reason == "Other" and not note:
		frappe.throw(_("Say why the drawer is opened"))
	pos_opening_shift = _open_session(pos_profile, pos_opening_shift)
	row = till_printer(pos_profile)
	if not row:
		frappe.throw(_("No CloudPRNT printer is set for this till"))
	result = jobs.enqueue(
		row,
		"",
		open_drawer="start",
		token=f"drawer-{frappe.generate_hash(length=12)}",
		valid_seconds=jobs.DRAWER_VALID_SECONDS,
	)
	opening = _record(pos_profile, reason, note, pos_opening_shift, row, result["job_token"])
	return {"opening": opening, "printer": row.label}


@frappe.whitelist()
def opening_status(opening):
	"""Where the opening `opening` stands: Sent (waiting for the printer), Opened or Not opened."""
	guards.require("Sales Invoice", "create")
	row = frappe.db.get_value(DOCTYPE, opening, ["user", "result", "result_detail", "printer"], as_dict=True)
	if not row:
		frappe.throw(_("{0} {1} not found").format(_(DOCTYPE), opening), frappe.DoesNotExistError)
	if row.user != frappe.session.user:
		guards.require(DOCTYPE, "read")
	return {"result": row.result, "label": _(row.result), "detail": row.result_detail, "printer": row.printer}


@frappe.whitelist()
def shift_summary(pos_opening_shift):
	"""The drawer openings of till session `pos_opening_shift`, for its closing: all, without a sale, not opened."""
	guards.require("Sales Invoice", "create")
	if frappe.db.exists("DocType", "POS Opening Shift"):
		owner = frappe.db.get_value("POS Opening Shift", pos_opening_shift, "user")
		if owner != frappe.session.user:
			guards.require(DOCTYPE, "read")
	rows = frappe.get_all(DOCTYPE, filters={"pos_opening_shift": pos_opening_shift}, fields=["reason", "result"])
	return {
		"total": len(rows),
		"without_sale": sum(1 for row in rows if row.reason != SALE),
		"not_opened": sum(1 for row in rows if row.result == NOT_OPENED),
	}


def sale_opened(doc):
	"""Whether the drawer was already opened for sale `doc`."""
	return bool(frappe.db.exists(DOCTYPE, {"reference_doctype": doc.doctype, "reference_name": doc.name, "reason": SALE}))


def open_for_sale(doc, row):
	"""Open the drawer for sale `doc` on its till's printer `row`, without a receipt (the till prints none by
	itself). A sale opens it once."""
	if sale_opened(doc):
		return None
	result = jobs.enqueue(
		row,
		"",
		open_drawer="start",
		token=f"drawer-{frappe.generate_hash(length=12)}",
		valid_seconds=jobs.DRAWER_VALID_SECONDS,
	)
	record_sale_opening(doc, row, result["job_token"])
	return result


def record_sale_opening(doc, row, job_token):
	"""Journal the drawer opening a cash sale's receipt asks for."""
	_record(
		doc.get("pos_profile"),
		SALE,
		"",
		doc.get("posa_pos_opening_shift") or "",
		row,
		job_token,
		reference=(doc.doctype, doc.name),
	)


def job_finished(tokens, result, detail=""):
	"""The print queue's answer for the jobs `tokens`: their openings still waiting take `result`."""
	tokens = [token for token in tokens or [] if token]
	if not tokens:
		return
	frappe.db.sql(
		f"""update `tab{DOCTYPE}` set result = %(result)s, result_detail = %(detail)s
		where print_job in %(tokens)s and result = %(sent)s""",
		{"result": result, "detail": (detail or "")[:140], "tokens": tuple(tokens), "sent": SENT},
	)


def till_printer(pos_profile):
	"""The printer row that prints the receipts of till `pos_profile`, or None."""
	for row in all_printers():
		if pos_profile and row.pos_profile == pos_profile:
			return row
	return None


def _open_session(pos_profile, pos_opening_shift):
	"""The caller's open till session on `pos_profile` (POS Next's POS Opening Shift). Without POS Next, the
	permission to sell is the only gate."""
	if not frappe.db.exists("DocType", "POS Opening Shift"):
		return pos_opening_shift or ""
	filters = {"user": frappe.session.user, "pos_profile": pos_profile, "status": "Open", "docstatus": 1}
	if pos_opening_shift:
		filters["name"] = pos_opening_shift
	shift = frappe.db.get_value("POS Opening Shift", filters, "name")
	if not shift:
		frappe.throw(_("Open a till session first"), frappe.PermissionError)
	return shift


def _record(pos_profile, reason, note, pos_opening_shift, row, job_token, reference=(None, None)):
	opening = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"opened_at": now_datetime(),
			"pos_profile": pos_profile or None,
			"user": frappe.session.user,
			"pos_opening_shift": pos_opening_shift or "",
			"reason": reason,
			"note": note or "",
			"reference_doctype": reference[0],
			"reference_name": reference[1],
			"printer": row.label,
			"print_job": job_token,
			"result": SENT,
		}
	)
	# A journal line is written whatever became of what it names (a till renamed, a sale deleted since).
	opening.flags.ignore_links = True
	opening.insert(ignore_permissions=True)
	return opening.name
