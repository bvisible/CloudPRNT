"""The provisional ticket of a table order: what the table ordered and what it comes to, before it pays.

A restaurant hands the table a priced summary before cashing in. A table (or takeaway) order of POS Next is a draft
Sales Invoice, which the till saves first, as its screen shows it; the ticket is built here from that invoice, so the
VAT of each rate and the total are the invoice's own, the total rounded as the till rounds it (CHF to 0.05).

It is not a receipt and says so: it shows no payment and no invoice barcode, and it never opens the cash drawer, even
on a printer set to open it with every sale. Like a receipt it expires: a ticket the printer did not take within a few
minutes (printer off, network down) is not printed by surprise later, when the table has paid and left.
"""

import frappe
from frappe import _
from frappe.utils import flt, fmt_money, formatdate, now_datetime

from cloudprnt import guards
from cloudprnt import print_queue_manager as jobs
from cloudprnt.api import _printer_for_profile
from cloudprnt.kitchen_ticket import _modifiers, _plain, _where
from cloudprnt.printers import find_printer
from cloudprnt.receipt_markup import LINE_CHARS, _column, _qty, _tax_label, _text, _width


@frappe.whitelist()
def print_provisional_ticket(invoice, printer=None):
	"""Print the provisional ticket of table order `invoice` (a draft Sales Invoice) on `printer`, else on the
	printer of the order's till. Never on the site's default printer: it may stand in another room."""
	guards.require("Sales Invoice", "read")
	guards.require_desk_account()
	doc = _open_order(invoice)
	row = find_printer(printer) if printer else _printer_for_profile(doc.get("pos_profile"))
	if not row:
		frappe.throw(
			_("No CloudPRNT printer prints the receipts of the till {0}").format(doc.get("pos_profile") or _("(none)"))
		)
	paper = row.paper_width or frappe.db.get_single_value("CloudPRNT Settings", "default_paper_width")
	result = jobs.enqueue(
		row,
		get_provisional_ticket_markup(doc, paper_width=paper),
		reference_doctype="Sales Invoice",
		reference_name=doc.name,
		# open_drawer is left out: a provisional ticket never opens the cash drawer
		valid_seconds=jobs.RECEIPT_VALID_MINUTES * 60,
	)
	return {
		"success": True,
		"message": _("Provisional ticket sent to {0}").format(row.label),
		"printer": row.label,
		**result,
	}


def _open_order(invoice):
	"""Table order `invoice`, which the caller may read: of a restaurant he may read, still open, with lines."""
	# A name, nothing else: a dict from a JSON body would be read as filters by frappe.db.exists
	if not invoice or not isinstance(invoice, str) or not frappe.db.exists("Sales Invoice", invoice):
		frappe.throw(_("{0} {1} not found").format(_("Sales Invoice"), invoice), frappe.DoesNotExistError)
	guards.require("Sales Invoice", "read", invoice)
	doc = frappe.get_doc("Sales Invoice", invoice)
	_require_restaurant_of(doc)
	_check_order(doc)
	return doc


def _check_order(doc):
	"""Refuse what is not an open table or takeaway order with lines: the ticket is printed before the table pays."""
	if doc.docstatus != 0:
		frappe.throw(_("{0} is no longer an open order").format(doc.name))
	if not (doc.get("restaurant_table") or doc.get("is_takeaway")):
		frappe.throw(_("{0} is not a table or takeaway order").format(doc.name))
	if not doc.get("items"):
		frappe.throw(_("Order {0} has no lines: there is nothing to print").format(doc.name))


def _restaurants_known():
	"""Whether this site's POS Next knows restaurants (NeoRestau): a site with an older one, or none, has none."""
	try:
		from pos_next.restaurant import permissions, scope  # noqa: F401
	except ImportError:
		return False
	return bool(frappe.db.exists("DocType", "Restaurant"))


def _require_restaurant_of(doc):
	"""Refuse an order of a restaurant the caller may not read (POS Next's per-restaurant user permissions): a user
	kept to one restaurant prints no other restaurant's order. An order belongs to the restaurant of its table, else
	to the one of the till that took it, else to the default restaurant (pos_next.restaurant.scope.keep_orders_of)."""
	if not _restaurants_known():
		return
	from pos_next.restaurant import permissions, scope
	from pos_next.restaurant.settings import default_restaurant, restaurant_for_profile

	if doc.get("restaurant_table"):
		restaurant = scope.restaurant_of_record("Restaurant Table", doc.restaurant_table)
	else:
		restaurant = restaurant_for_profile(doc.get("pos_profile")) or default_restaurant()
	permissions.require_restaurant(restaurant)


def get_provisional_ticket_markup(doc, paper_width=80):
	"""Star Document Markup of the provisional ticket of table order `doc`."""
	chars = LINE_CHARS.get(_width(paper_width), 48)
	rule = "-" * chars

	def money(value):
		return fmt_money(flt(value), currency=doc.currency).strip()

	now = now_datetime()
	out = [
		"[align: centre]",
		f"[magnify: width 2; height 1]{_text(doc.company)}[magnify]",
		"",
		f"[magnify: width 2; height 2][bold: on]{_text(_('PROVISIONAL TICKET'))}[bold: off][magnify]",
		# The label of the table, or "Takeaway" and the order's number
		f"[magnify: width 2; height 2]{_text(_where(doc))}[magnify]",
		"[align: left]",
		"",
		f"{_('Date')}: {formatdate(now.date())} {now.strftime('%H:%M')}",
		rule,
	]
	for line in doc.items:
		left = f"{_qty(line.qty)} x {line.item_name or line.item_code}"
		right = money(line.amount)
		if len(left) + len(right) + 1 <= chars:
			out.append(_column(left, right))
		else:
			# A name too long for the line: on its own (the printer wraps it), the amount on the next line
			out += [_text(left), _column("", right)]
		for remark in _modifiers(line.get("posa_item_modifiers")) + _plain(line.get("posa_special_instructions")):
			out.append(f"   {_text(remark)}")
	out.append(rule)

	# The totals as the invoice holds them, laid out as on its receipt
	if flt(doc.get("discount_amount")):
		out.append(_column(f"{_('Discount')}:", money(doc.discount_amount)))
	taxes = [tax for tax in doc.get("taxes") or [] if flt(tax.tax_amount)]
	if any(not tax.get("included_in_print_rate") for tax in taxes):
		out.append(_column(f"{_('Net Total')}:", money(doc.net_total)))
	abbr = frappe.get_cached_value("Company", doc.company, "abbr") or ""
	for tax in taxes:
		out.append(_column(f"{_tax_label(tax, abbr)}:", money(tax.tax_amount)))
	# What the table will pay: the total rounded as the till rounds it (0 when the till does not round)
	total = flt(doc.get("rounded_total")) or flt(doc.grand_total)
	out += [
		f"[magnify: width 1; height 2][bold: on]{_column(_('Total') + ':', money(total))}[bold: off][magnify]",
		rule,
		"[align: centre]",
		f"[magnify: width 1; height 2][bold: on]{_text(_('THIS IS NOT A RECEIPT'))}[bold: off][magnify]",
		"[align: left]",
		"[cut: feed; partial]",
	]
	return "\n".join(out)
