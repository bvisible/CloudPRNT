"""What the desk and the POS call to print on a CloudPRNT printer and to open its cash drawer."""

import frappe
from frappe import _

from cloudprnt import drawer, guards
from cloudprnt import print_queue_manager as jobs
from cloudprnt.printers import all_printers, find_printer
from cloudprnt.receipt_markup import get_receipt_markup, is_cash_sale

RECEIPT_DOCTYPES = ("Sales Invoice", "POS Invoice")

#: When the cash drawer opens with a receipt, per printer (CloudPRNT Printers.open_drawer).
DRAWER_FOR_CASH = "Cash payments"
DRAWER_ALWAYS = "Always"


@frappe.whitelist()
def print_receipt(doctype, name, printer=None):
	"""Print the till receipt of `doctype` `name` on `printer`, else on the printer of its till (POS Profile),
	else on the default printer. The cash drawer opens as that printer is set to."""
	if doctype not in RECEIPT_DOCTYPES:
		frappe.throw(_("Receipts are printed for {0} only").format(", ".join(_(d) for d in RECEIPT_DOCTYPES)))
	# A receipt prints at the till: whoever makes sales there (a customer reading their own invoice does not).
	guards.require(doctype, "create")
	if not frappe.db.exists(doctype, name):
		frappe.throw(_("{0} {1} not found").format(_(doctype), name), frappe.DoesNotExistError)
	guards.require(doctype, "read", name)
	doc = frappe.get_doc(doctype, name)
	row = find_printer(printer) if printer else (_printer_for_profile(doc.get("pos_profile")) or find_printer())
	return _queue_receipt(doc, row)


@frappe.whitelist()
def print_pos_invoice(invoice_name, printer=None, use_mqtt=False):
	"""Print the receipt of POS Invoice `invoice_name` (the POS Invoice form's button)."""
	guards.require("POS Invoice", "create")
	return print_receipt("POS Invoice", invoice_name, printer)


@frappe.whitelist()
def open_cash_drawer(printer=None):
	"""Open the cash drawer wired to `printer` (the default printer when none), without printing."""
	guards.require("Sales Invoice", "create")
	row = find_printer(printer)
	result = jobs.enqueue(
		row,
		"",
		open_drawer="start",
		token=f"drawer-{frappe.generate_hash(length=12)}",
		valid_seconds=jobs.DRAWER_VALID_SECONDS,
	)
	# Every opening is journaled, this one from the desk too.
	drawer._record(
		row.pos_profile, "Other", _("Opened from the CloudPRNT settings"), "", row, result["job_token"]
	)
	return {"success": True, "message": _("Cash drawer opening sent to {0}").format(row.label), **result}


def print_on_submit(doc, method=None):
	"""doc_events on_submit: a POS sale prints its receipt on its till's printer where that printer is set to print
	receipts by itself, and opens the till's drawer as the printer is set to, with or without a receipt. A sale made
	offline and synced later does neither: its customer has left. Never stops the sale: a failure is logged."""
	if doc.doctype == "Sales Invoice" and not doc.get("is_pos"):
		return
	profile = doc.get("pos_profile")
	# Set by POS Next on a sale it syncs from a till that was offline.
	if not profile or doc.flags.get("pos_next_offline_sync"):
		return
	try:
		for row in all_printers():
			if row.pos_profile != profile:
				continue
			if row.auto_print:
				_queue_receipt(doc, row)
			elif _drawer_opens(doc, row):
				drawer.open_for_sale(doc, row)
	except Exception:
		frappe.log_error(f"CloudPRNT: the receipt of {doc.name} was not queued", frappe.get_traceback())


def _drawer_opens(doc, row):
	"""Whether sale `doc` opens the drawer of printer `row` (CloudPRNT Printers.open_drawer)."""
	rule = row.open_drawer or DRAWER_FOR_CASH
	return rule == DRAWER_ALWAYS or (rule == DRAWER_FOR_CASH and is_cash_sale(doc))


def _queue_receipt(doc, row):
	# A sale opens the drawer once: its receipt printed again later leaves the drawer shut.
	open_drawer = "end" if _drawer_opens(doc, row) and not drawer.sale_opened(doc) else None
	paper = row.paper_width or frappe.db.get_single_value("CloudPRNT Settings", "default_paper_width")
	result = jobs.enqueue(
		row,
		get_receipt_markup(doc, paper_width=paper),
		reference_doctype=doc.doctype,
		reference_name=doc.name,
		open_drawer=open_drawer,
		valid_seconds=jobs.RECEIPT_VALID_MINUTES * 60,
	)
	if open_drawer:
		drawer.record_sale_opening(doc, row, result["job_token"])
	return {
		"success": True,
		"message": _("Receipt {0} sent to {1}").format(doc.name, row.label),
		"printer": row.label,
		**result,
	}


def _printer_for_profile(profile):
	if not profile:
		return None
	for row in all_printers():
		if row.pos_profile == profile:
			return row
	return None
