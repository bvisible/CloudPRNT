"""The print queue: the jobs waiting for a printer, and what became of each.

A job is Pending until its printer fetches it (Fetched), then Printed or Failed by the code the printer
confirms it with. It is kept as Star Document Markup and rendered when fetched, in the format the printer
asks for (render.py). A job is held by its printer row; jobs queued by MAC address alone (by callers written
before the rows had names to hold them) go to the printer that has that MAC.
"""

import json
import re
import uuid
from urllib.parse import unquote

import frappe
from frappe import _
from frappe.utils import add_to_date, now_datetime

from cloudprnt import render
from cloudprnt.printers import find_printer, normalize_mac, printer_for_mac

DOCTYPE = "CloudPRNT Print Queue"
MARKUP = "Markup"
COMMANDS = "Printer Commands"

#: A job fetched and never confirmed is marked failed after this long, rather than printed a second time.
UNCONFIRMED_AFTER_MINUTES = 10
#: Printed and failed jobs are kept this long, for support, then deleted.
KEEP_DAYS = 30

_HEX = re.compile(r"^[0-9A-Fa-f\s]+$")
_FIELDS = [
	"name",
	"job_token",
	"printer",
	"printer_mac",
	"status",
	"job_format",
	"job_data",
	"media_types",
	"open_drawer",
	"buzzer",
	"reference_doctype",
	"reference_name",
	"attempts",
	"creation",
]


def enqueue(
	printer, markup, reference_doctype=None, reference_name=None, open_drawer=None, buzzer=0, token=None
):
	"""Queue `markup` (Star Document Markup) for `printer` (row, row name, label or MAC; the default printer
	when empty). open_drawer: "start" or "end" opens the cash drawer at that end of the job."""
	row = printer if isinstance(printer, dict) else find_printer(printer)
	return _insert(
		row,
		token=token or uuid.uuid4().hex,
		job_format=MARKUP,
		job_data=markup or "",
		open_drawer=open_drawer if open_drawer in ("start", "end") else "",
		buzzer=int(buzzer or 0),
		reference_doctype=reference_doctype,
		reference_name=reference_name,
	)


def add_job_to_queue(job_token, printer_mac, invoice_name=None, job_data=None, media_types=None):
	"""Queue a job the way callers written before enqueue() do (the payments app's terminal receipt).

	job_data is Star Document Markup, or printer commands as hex for the media types listed; with no job_data
	the receipt of POS Invoice `invoice_name` is printed.
	"""
	try:
		row = printer_for_mac(printer_mac) or find_printer(printer_mac)
		if job_data and len(job_data) > 100 and _HEX.match(job_data):
			return _insert(
				row,
				token=job_token,
				job_format=COMMANDS,
				job_data=re.sub(r"\s", "", job_data),
				media_types=json.dumps(media_types or ["application/vnd.star.starprnt"]),
			)
		if not job_data and invoice_name:
			from cloudprnt.receipt_markup import get_receipt_markup

			job_data = get_receipt_markup("POS Invoice", invoice_name)
		return enqueue(
			row,
			job_data,
			reference_doctype="POS Invoice" if invoice_name else None,
			reference_name=invoice_name,
			token=job_token,
		)
	except Exception as error:
		frappe.log_error("CloudPRNT: a job could not be queued", frappe.get_traceback())
		return {"success": False, "message": str(error)}


def _insert(row, token, **values):
	job = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"job_token": token,
			"printer": row.name,
			"printer_mac": normalize_mac(row.mac_address) or "",
			"status": "Pending",
			"invoice_name": values.get("reference_name")
			if values.get("reference_doctype") in ("POS Invoice", "Sales Invoice")
			else None,
			**values,
		}
	)
	job.insert(ignore_permissions=True)
	return {"success": True, "job_token": token, "queue_position": queue_position(row, token)}


def _for_printer(row):
	"""SQL condition and values matching the jobs of printer `row`."""
	return (
		"(printer = %(printer)s or (ifnull(printer, '') = '' and printer_mac = %(mac)s))",
		{"printer": row.name, "mac": normalize_mac(row.mac_address) or "-"},
	)


def next_job(row):
	"""The oldest job waiting for printer `row`, or None."""
	condition, values = _for_printer(row)
	jobs = frappe.db.sql(
		f"""select {', '.join(_FIELDS)} from `tab{DOCTYPE}`
		where status = 'Pending' and {condition} order by creation asc limit 1""",
		values,
		as_dict=True,
	)
	return jobs[0] if jobs else None


def get_job(row, token=None, statuses=("Pending", "Fetched")):
	"""Printer `row`'s job `token`; without a token (clients that send none), its oldest job in `statuses`."""
	condition, values = _for_printer(row)
	values.update(token=token or "", statuses=statuses)
	where = "job_token = %(token)s" if token else "status in %(statuses)s"
	jobs = frappe.db.sql(
		f"""select {', '.join(_FIELDS)} from `tab{DOCTYPE}`
		where {where} and {condition} order by creation asc limit 1""",
		values,
		as_dict=True,
	)
	return jobs[0] if jobs else None


def queue_position(row, token):
	condition, values = _for_printer(row)
	tokens = frappe.db.sql_list(
		f"select job_token from `tab{DOCTYPE}` where status = 'Pending' and {condition} order by creation asc",
		values,
	)
	return tokens.index(token) + 1 if token in tokens else 0


def media_types_for(job, row):
	"""The formats job `job` can be fetched in by printer `row`."""
	if job.job_format == COMMANDS:
		try:
			return json.loads(job.media_types or "[]") or ["application/vnd.star.starprnt"]
		except ValueError:
			return ["application/vnd.star.starprnt"]
	return render.offered_media_types(printer_encodings(row))


def printer_encodings(row):
	try:
		return json.loads(row.media_types or "[]")
	except ValueError:
		return []


def render_job(job, media_type, row):
	"""The bytes of `job` in `media_type` for printer `row`. Raises render.RenderError."""
	if media_type not in media_types_for(job, row):
		raise render.RenderError(f"Job {job.job_token} is not offered as {media_type}")
	if job.job_format == COMMANDS:
		return bytes.fromhex(job.job_data or "")
	return render.render(
		job.job_data,
		media_type,
		paper_width=row.paper_width or frappe.db.get_single_value("CloudPRNT Settings", "default_paper_width"),
		print_dots=row.print_dots,
		drawer=job.open_drawer,
		buzzer=job.buzzer,
	)


def mark_fetched(job, media_type):
	frappe.db.set_value(
		DOCTYPE,
		job.name,
		{
			"status": "Fetched",
			"media_type": media_type,
			"fetched_at": now_datetime(),
			"attempts": (job.attempts or 0) + 1,
		},
		update_modified=False,
	)


def mark_confirmed(job, code):
	"""Record the code a printer confirmed `job` with: 2xx is printed, anything else failed."""
	code = unquote(str(code or "")).strip()
	printed = not code or code[:1] == "2"
	frappe.db.set_value(
		DOCTYPE,
		job.name,
		{
			"status": "Printed" if printed else "Failed",
			"result_code": code[:140],
			"printed_at": now_datetime() if printed else None,
		},
		update_modified=False,
	)
	return printed


def fail_unconfirmed_jobs():
	"""Scheduled: a job fetched long ago and never confirmed did not print as far as anyone knows."""
	frappe.db.sql(
		f"""update `tab{DOCTYPE}` set status = 'Failed', result_code = %(code)s
		where status = 'Fetched' and fetched_at < %(before)s""",
		{
			"code": "No confirmation from the printer",
			"before": add_to_date(now_datetime(), minutes=-UNCONFIRMED_AFTER_MINUTES),
		},
	)


def delete_old_jobs():
	"""Scheduled: printed and failed jobs go after KEEP_DAYS."""
	frappe.db.delete(
		DOCTYPE,
		{"status": ("in", ("Printed", "Failed")), "creation": ("<", add_to_date(now_datetime(), days=-KEEP_DAYS))},
	)


@frappe.whitelist()
def print_again(job):
	"""Queue a printed or failed job once more, as a new job (from the queue's form)."""
	from cloudprnt import guards

	guards.require("CloudPRNT Settings", "write")
	guards.require(DOCTYPE, "read")
	source = frappe.get_doc(DOCTYPE, job)
	row = frappe._dict(name=source.printer, mac_address=source.printer_mac)
	if source.printer:
		from cloudprnt.printers import get_printer

		row = get_printer(source.printer) or row
	values = {
		field: source.get(field)
		for field in ("job_format", "job_data", "media_types", "buzzer", "reference_doctype", "reference_name")
	}
	# A copy of a receipt does not open the cash drawer a second time.
	result = _insert(row, token=uuid.uuid4().hex, open_drawer="", **values)
	frappe.msgprint(_("The job was queued again"), alert=True)
	return result
