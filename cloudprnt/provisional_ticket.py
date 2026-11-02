"""The provisional ticket of a table or takeaway order: what the table ordered and what it comes to, before it pays.

The ticket prints the order AS THE TILL SHOWS IT, and printing it saves nothing (ruling 93). The till sends its order the
way its payment sends it: each line at its rate after its discount, the order's discount as the payment applies it
(a whole-cart coupon included). The server builds an unsaved Sales Invoice from it, priced by ERPNext's own computation
the way POS Next prices the payment's draft (`pos_next.api.invoices.update_invoice`: pricing rules ignored, rounding as
the till's POS Profile says, `set_missing_values(for_validate=True)`, `calculate_taxes_and_totals`, then the VAT included
or on top as the till's POS Settings say, POS Next's validate hook `apply_tax_inclusive`). Nothing is inserted, saved or
committed; nothing reaches the kitchen. Lines not sent to
the kitchen yet are on the bill and wait for « Validate »; a dish removed on the screen is not on the bill, and the
kitchen hears of it at « Validate ». The table's open order is only read: its table, and what a guest already paid by
phone (printed as « Already paid » and « Left to pay », the controller's ruling).

What the till sends is read before it is priced (`_parse`): no more than MAX_LINES lines, every amount a finite number
within MAX_AMOUNT, every text a text, a remark cut at MAX_REMARK_CHARS on the paper, a discount no larger than what it takes
off, at most PRINTS_PER_MINUTE tickets per user per minute, a price set by hand only where the payment would take it, and a
cashier prints on the tills of his own restaurant only. Whatever is wrong is a refusal the till shows, never a crash.

It is not a receipt and says so: no payment of the till, no barcode, and it never opens the cash drawer, even on a
printer set to open it with every sale. It prints on the receipt printer of the till that sends it, never on a printer
the caller names. Like a receipt it expires: a ticket the printer did not take within RECEIPT_VALID_MINUTES is not
printed by surprise later, when the table has paid and left. The till follows the job (`ticket_status`) to show the
waiter whether it printed (ruling 97).
"""

import json
import math

import frappe
from frappe import _
from frappe.utils import cint, flt, fmt_money, formatdate, now_datetime

from cloudprnt import guards
from cloudprnt import print_queue_manager as jobs
from cloudprnt.api import _printer_for_profile
from cloudprnt.kitchen_ticket import _modifiers, _plain, _where
from cloudprnt.printers import get_printer
from cloudprnt.receipt_markup import LINE_CHARS, _column, _qty, _text, _width, charged_taxes, printable, vat_lines

#: What the ticket reads of a line the till sends (the fields of its payment's lines: useInvoice.formatItemsForSubmission).
LINE_FIELDS = (
	"item_code",
	"item_name",
	"qty",
	"rate",
	"price_list_rate",
	"uom",
	"conversion_factor",
	"discount_percentage",
	"discount_amount",
	"is_free_item",
	"is_rate_manually_edited",
	"warehouse",
	"posa_item_modifiers",
	"posa_special_instructions",
	# A row offered by the cashier (NeoResto lot 1, task 24): it charges nothing and says « Offered »
	"posa_is_offered",
)


#: What a till may ask a ticket to print. A tablet sends an order; anything else can send anything, and the paper, the
#: printer and the queue are not for a flood.
#: The most lines an order holds.
MAX_LINES = 200
#: A remark or an option is cut here on the paper (a longer text is a person's essay, not a note for the table).
MAX_REMARK_CHARS = 500
#: The most rows of options and remarks printed under one dish.
MAX_REMARK_ROWS = 20
#: A dish's name is cut at the length of an item name.
MAX_NAME_CHARS = 140
#: A text field far beyond any remark refuses the order, instead of being read.
MAX_FIELD_CHARS = 5000
#: A quantity, a price or a discount is a finite number of at most this size.
MAX_AMOUNT = 1_000_000
#: The tickets one user may ask for in a minute (the button polls the status of a ticket, which is not counted).
PRINTS_PER_MINUTE = 20
RATE_WINDOW_SECONDS = 60

#: How the fields of a line the till sends are read.
NUMBER_FIELDS = ("qty", "rate", "price_list_rate", "conversion_factor", "discount_percentage", "discount_amount")
TEXT_FIELDS = ("item_name", "uom", "warehouse", "posa_item_modifiers", "posa_special_instructions")
FLAG_FIELDS = ("is_free_item", "is_rate_manually_edited", "posa_is_offered")


class NoTicketPrinter(frappe.ValidationError):
	"""The till has no CloudPRNT receipt printer: the till prints the ticket itself (its browser) instead."""


@frappe.whitelist()
def print_provisional_ticket(order):
	"""Print the provisional ticket of `order` (JSON), the order the till shows: `pos_profile` (the till), the table
	(`restaurant_table`) or the takeaway order (`is_takeaway`, `takeaway_number`), `draft` (the open order the server
	holds for it, read only), `customer`, `discount_amount` and `items`, shaped as the till's payment sends them.
	Prints on the receipt printer of that till.

	Whatever is wrong with the order is a refusal the till shows (a ValidationError, by its plain name), never a crash
	that sends the waiter to the browser's print window: a kind of ValidationError the till does not know by name
	(LinkValidationError, MandatoryError...) is raised again as a plain one with the same words."""
	guards.require("Sales Invoice", "create")
	guards.require_desk_account()
	_throttle()
	try:
		return _print(order)
	except (NoTicketPrinter, frappe.DoesNotExistError):
		raise
	except frappe.ValidationError as error:
		if type(error) is frappe.ValidationError:
			raise
		message = str(error)
		frappe.clear_last_message()
		frappe.throw(message, frappe.ValidationError)


def _print(order):
	order = _parse(order)
	profile = _till(order)
	restaurant = _restaurant_of(order, profile.name)
	_require_own_restaurant(restaurant)
	draft = _open_order(order, profile.name)
	_require_rates_the_till_allows(order, profile)
	row = _printer_for_profile(profile.name)
	if not row:
		frappe.throw(_("No CloudPRNT printer prints the receipts of the till {0}").format(profile.name), NoTicketPrinter)
	doc = build_unsaved_invoice(order, profile)
	if flt(doc.grand_total) < 0 or flt(doc.get("rounded_total")) < 0:
		# a discount beyond the order's total
		_invalid("total")
	paid = already_paid(draft, restaurant)
	paper = row.paper_width or frappe.db.get_single_value("CloudPRNT Settings", "default_paper_width")
	result = jobs.enqueue(
		row,
		get_provisional_ticket_markup(doc, paper_width=paper, paid=paid),
		reference_doctype="Sales Invoice" if draft else None,
		reference_name=draft.name if draft else None,
		# open_drawer is left out: a provisional ticket never opens the cash drawer
		valid_seconds=jobs.RECEIPT_VALID_MINUTES * 60,
	)
	return {
		"success": True,
		"message": _("Provisional ticket sent to {0}").format(row.label),
		"printer": row.label,
		"printer_online": bool(cint(row.online)),
		"valid_minutes": jobs.RECEIPT_VALID_MINUTES,
		"total": flt(doc.rounded_total) or flt(doc.grand_total),
		**result,
	}


def _throttle():
	"""At most PRINTS_PER_MINUTE tickets per user per minute (RateLimitExceededError, which the till shows as a refusal).
	A count that kept no expiry (a worker killed between its two calls) is given one, so that nobody stays locked out."""
	cache = frappe.cache()
	key = cache.make_key(f"cloudprnt_provisional_ticket_rate:{frappe.session.user}")
	count = cache.incrby(key, 1)
	if cache.ttl(key) < 0:
		cache.expire(key, RATE_WINDOW_SECONDS)
	if count > PRINTS_PER_MINUTE:
		frappe.throw(_("Too many tickets in a minute: wait a moment before printing another"), frappe.RateLimitExceededError)


@frappe.whitelist()
def ticket_status(job):
	"""Where the ticket `job` (its job token) stands, for the till that printed it: Pending, Fetched (the printer took
	it), Printed, Failed or Expired, whether its printer is online, and why it did not print. Only the status of a job
	of a printer of a till the caller works at."""
	guards.require("Sales Invoice", "create")
	guards.require_desk_account()
	if not job or not isinstance(job, str):
		frappe.throw(_("{0} {1} not found").format(_("CloudPRNT Print Queue"), job), frappe.DoesNotExistError)
	row = frappe.db.get_value(jobs.DOCTYPE, {"job_token": job}, ["status", "printer", "result_code"], as_dict=True)
	printer = get_printer(row.printer) if row and row.printer else None
	if not row or not printer or not printer.pos_profile:
		frappe.throw(_("{0} {1} not found").format(_("CloudPRNT Print Queue"), job), frappe.DoesNotExistError)
	_require_till_access(printer.pos_profile)
	_require_own_restaurant(_restaurant_of_till(printer.pos_profile))
	return {
		"status": row.status,
		"printer_online": bool(cint(printer.online)),
		"reason": row.result_code if row.status in ("Failed", "Expired") else "",
	}


def _parse(order):
	"""The order the till sent, read: a JSON dict with lines, for a table or a takeaway order, within the limits (MAX_LINES,
	MAX_AMOUNT...). Every value is read as what it must be, a refusal when it is not, so that no payload reaches the pricing as
	a dict where a code was expected, or as NaN where a quantity was."""
	if isinstance(order, str):
		try:
			order = json.loads(order)
		except ValueError:
			order = None
	if not isinstance(order, dict):
		frappe.throw(_("The order to print is not readable"))
	lines = order.get("items")
	if not isinstance(lines, list) or not any(isinstance(line, dict) and line.get("item_code") for line in lines):
		frappe.throw(_("This order has no lines: there is nothing to print"))
	if len(lines) > MAX_LINES:
		frappe.throw(_("An order of more than {0} lines cannot be printed").format(MAX_LINES))
	if not (order.get("restaurant_table") or cint(order.get("is_takeaway"))):
		frappe.throw(_("Only a table or takeaway order has a provisional ticket"))
	order = dict(order)
	order["items"] = [_clean_line(line) for line in lines if isinstance(line, dict) and line.get("item_code")]
	if order.get("discount_amount") not in (None, ""):
		order["discount_amount"] = _number(order["discount_amount"], "discount_amount")
	number = order.get("takeaway_number")
	order["takeaway_number"] = _text_value(number, "takeaway_number") if isinstance(number, (str, int, float)) else ""
	return order


def _clean_line(line):
	"""A line of the order, with only the fields of a payment's line, each read as what it is: a code is a text, an amount a
	finite number of at most MAX_AMOUNT (a number written as text is read), a remark a text (a number is printed as one), a flag a
	flag. A discount takes off no more than the line comes to."""
	code = line.get("item_code")
	if not isinstance(code, str) or not code.strip():
		_invalid("item_code")
	clean = {"item_code": code}
	for field in NUMBER_FIELDS:
		if line.get(field) not in (None, ""):
			clean[field] = _number(line[field], field)
	for field in TEXT_FIELDS:
		if line.get(field) is not None:
			clean[field] = _text_value(line[field], field)
	for field in FLAG_FIELDS:
		if line.get(field) is not None:
			clean[field] = cint(line[field])
	if not 0 <= clean.get("discount_percentage", 0) <= 100:
		_invalid("discount_percentage")
	discount = clean.get("discount_amount", 0)
	gross = clean.get("qty", 0) * max(clean.get("price_list_rate", 0), clean.get("rate", 0))
	if discount < 0 or (gross > 0 and discount > gross + 0.01):
		_invalid("discount_amount")
	return clean


def _number(value, field):
	"""A finite number of at most MAX_AMOUNT, from a number or a number written as text; any other value refuses the order."""
	if isinstance(value, bool) or not isinstance(value, (int, float, str)):
		_invalid(field)
	try:
		number = float(value)
	except (ValueError, OverflowError):
		_invalid(field)
	if not math.isfinite(number) or abs(number) > MAX_AMOUNT:
		_invalid(field)
	return number


def _text_value(value, field):
	"""A text of at most MAX_FIELD_CHARS; a number is read as the text it is written as; any other value refuses the order."""
	if isinstance(value, bool) or not isinstance(value, (str, int, float)):
		_invalid(field)
	text = value if isinstance(value, str) else str(value)
	if len(text) > MAX_FIELD_CHARS:
		_invalid(field)
	return text


def _invalid(field):
	frappe.throw(_("The order to print holds a value that is not valid ({0})").format(field))


def _cut(text, limit):
	"""`text` made safe for the printer (printable), and cut at `limit` characters with « ... » when it is longer."""
	text = printable(text)
	return text if len(text) <= limit else text[: limit - 3] + "..."


def _till(order):
	"""The till that prints: the POS Profile the order names, which the caller must work at. A name, nothing else: a
	blank value would read as no till, a dict as filters."""
	name = order.get("pos_profile")
	if not isinstance(name, str) or not name.strip() or not frappe.db.exists("POS Profile", name):
		frappe.throw(_("The till of this order is not known"))
	_require_till_access(name)
	return frappe.get_cached_doc("POS Profile", name)


def _require_till_access(pos_profile):
	"""Whoever may work at till `pos_profile` (POS Next's rule: a desk account assigned to it, or reading sales invoices
	as every cashier does, or a till manager); a site without POS Next: whoever reads the POS Profile."""
	try:
		from pos_next.api.cash_entry import require_till_access
	except ImportError:
		guards.require("POS Profile", "read", pos_profile)
		return
	require_till_access(pos_profile)


def _restaurants_known():
	"""Whether this site's POS Next knows restaurants (NeoResto): a site with an older one, or none, has none."""
	try:
		from pos_next.restaurant import permissions, scope  # noqa: F401
	except ImportError:
		return False
	return bool(frappe.db.exists("DocType", "Restaurant"))


def _restaurant_of_till(pos_profile):
	"""The restaurant a till belongs to (the default restaurant for a till that names none, as an order's takeaway does);
	None when POS Next knows no restaurants."""
	if not _restaurants_known():
		return None
	from pos_next.restaurant.settings import default_restaurant, restaurant_for_profile

	return restaurant_for_profile(pos_profile) or default_restaurant()


def _require_own_restaurant(restaurant):
	"""A cashier prints on the tills of the restaurant he works for, and on no other's, even when no User Permission keeps
	him out of it: the restaurant of his open shift (pos_next.restaurant.scope.current_restaurant, the one every restaurant
	list of the till is read for; the default restaurant when no shift is open). Whoever runs every till, a till manager or
	a restaurant manager, is not held to one."""
	if not _restaurants_known():
		return
	from pos_next.restaurant import scope
	from pos_next.restaurant.settings import default_restaurant

	if _runs_every_till():
		return
	if (scope.current_restaurant() or default_restaurant()) != (restaurant or default_restaurant()):
		frappe.throw(_("Not permitted"), frappe.PermissionError)


def _runs_every_till():
	try:
		from pos_next.api.cash_entry import is_till_manager
		from pos_next.restaurant.permissions import MANAGER_ROLE
	except ImportError:
		return False
	return bool(is_till_manager() or MANAGER_ROLE in frappe.get_roles())


def _restaurant_of(order, pos_profile):
	"""The restaurant of the order, which the caller must be allowed to read (POS Next's per-restaurant permissions),
	and which must be the till's: a till prints the tables of its own restaurant only. A takeaway order belongs to the
	restaurant of its till, else to the default restaurant (pos_next.restaurant.scope.keep_orders_of). None when POS
	Next knows no restaurants."""
	table = order.get("restaurant_table")
	if table and (not isinstance(table, str) or not frappe.db.exists("Restaurant Table", table)):
		frappe.throw(_("{0} {1} not found").format(_("Restaurant Table"), table), frappe.DoesNotExistError)
	if not _restaurants_known():
		return None
	from pos_next.restaurant import permissions, scope
	from pos_next.restaurant.settings import default_restaurant, restaurant_for_profile

	till_restaurant = restaurant_for_profile(pos_profile)
	if table:
		restaurant = scope.restaurant_of_record("Restaurant Table", table)
		if restaurant != till_restaurant:
			frappe.throw(_("This till does not serve this table"), frappe.PermissionError)
	else:
		restaurant = till_restaurant or default_restaurant()
	permissions.require_restaurant(restaurant)
	return restaurant


def _open_order(order, pos_profile):
	"""The open order the server holds for the table (or the one the till names), read only; None when there is none
	yet. An order already paid or cancelled is refused: its table has nothing left to pay on it. A table's order is the
	table's; a takeaway order names only a takeaway order of the till that sends it (and so of the same restaurant), never
	another table's or a desk order."""
	name = order.get("draft")
	table = order.get("restaurant_table") or None
	if name is not None and not isinstance(name, str):
		frappe.throw(_("{0} {1} not found").format(_("Sales Invoice"), name), frappe.DoesNotExistError)
	if not name and table:
		name = frappe.db.get_value(
			"Sales Invoice", {"docstatus": 0, "restaurant_table": table}, "name", order_by="modified desc"
		)
	if not name:
		return None
	if not frappe.db.exists("Sales Invoice", name):
		frappe.throw(_("{0} {1} not found").format(_("Sales Invoice"), name), frappe.DoesNotExistError)
	guards.require("Sales Invoice", "read", name)
	draft = frappe.get_doc("Sales Invoice", name)
	if draft.docstatus != 0:
		frappe.throw(_("{0} is no longer an open order").format(draft.name))
	if table:
		its_own = (draft.get("restaurant_table") or None) == table
	else:
		its_own = (
			cint(draft.get("is_takeaway"))
			and not draft.get("restaurant_table")
			and draft.get("pos_profile") == pos_profile
		)
	if not its_own:
		frappe.throw(_("{0} is not the order of this table").format(draft.name))
	return draft


def _require_rates_the_till_allows(order, profile):
	"""A price set by hand follows the rules of the payment: POS Next refuses it when the till forbids editing a rate, or
	when it lowers the price beyond the till's maximum discount (pos_next.api.invoices.validate_manual_rate_edit, which
	update_invoice calls for every line). The ticket says no with the same words, so that it never prints a price the payment
	will refuse."""
	try:
		from pos_next.api.invoices import validate_manual_rate_edit
	except ImportError:
		return
	for line in order["items"]:
		verdict = validate_manual_rate_edit(line, profile.name)
		if not verdict.get("valid"):
			frappe.throw(verdict.get("message"))


def build_unsaved_invoice(order, profile):
	"""The order as an unsaved Sales Invoice of till `profile`, priced by ERPNext as the payment's draft is: the company,
	currency, price list and warehouse are the till's, never the caller's; each line keeps the rate the till sends (its
	rate after its discount), pricing rules are ignored, the total is rounded as the till rounds it. Never inserted."""
	customer = order.get("customer")
	if not isinstance(customer, str) or not frappe.db.exists("Customer", customer):
		# A customer typed at the till and not created yet (the payment creates it): the till's own prices the order
		customer = profile.customer
	doc = frappe.new_doc("Sales Invoice")
	doc.update(
		{
			"pos_profile": profile.name,
			"company": profile.company,
			"currency": profile.currency,
			"selling_price_list": profile.selling_price_list,
			"set_warehouse": profile.warehouse,
			"customer": customer,
			"is_pos": 1,
			"update_stock": 1,
			"discount_amount": max(flt(order.get("discount_amount")), 0),
			"restaurant_table": order.get("restaurant_table") or None,
			"is_takeaway": 1 if cint(order.get("is_takeaway")) else 0,
			"takeaway_number": str(order.get("takeaway_number") or ""),
		}
	)
	for line in order.get("items") or []:
		if isinstance(line, dict) and line.get("item_code"):
			doc.append("items", {field: line.get(field) for field in LINE_FIELDS if line.get(field) is not None})
	doc.flags.ignore_permissions = True
	ignore_account_permission = frappe.flags.ignore_account_permission
	frappe.flags.ignore_account_permission = True
	try:
		doc.ignore_pricing_rule = 1
		doc.flags.ignore_pricing_rule = True
		doc.disable_rounded_total = cint(profile.disable_rounded_total)
		doc.set_missing_values(for_validate=True)
		# set_missing_values() resets ignore_pricing_rule from the POS Profile, as update_invoice notes
		doc.ignore_pricing_rule = 1
		doc.flags.ignore_pricing_rule = True
		_takeaway_tax(doc)
		doc.calculate_taxes_and_totals()
		_tax_inclusive(doc)
	finally:
		frappe.flags.ignore_account_permission = ignore_account_permission
	return doc


def _tax_inclusive(doc):
	"""Prices with the VAT in them, or the VAT on top, as the till's POS Settings say (`tax_inclusive`), whatever its tax
	template says: POS Next's own rule, which its validate hook applies to the payment's draft (and recomputes the
	totals when it changes something)."""
	try:
		from pos_next.api.sales_invoice_hooks import apply_tax_inclusive
	except ImportError:
		return
	apply_tax_inclusive(doc)


def _takeaway_tax(doc):
	"""A takeaway order takes its restaurant's takeaway tax category, as the paid invoice will in its before_validate
	(POS Next, NeoResto lot 1, task 10), when that POS Next is installed."""
	try:
		from pos_next.restaurant.takeaway_tax import apply_takeaway_tax_category
	except ImportError:
		return
	apply_takeaway_tax_category(doc)


def already_paid(draft, restaurant=None):
	"""What a guest already paid by phone on the open order `draft`: its payments less the tips among them (the tip
	line is not the order's, as the till reads it: get_table_order). 0 without an open order."""
	if not draft:
		return 0.0
	tip_item = None
	if restaurant and _restaurants_known():
		from pos_next.restaurant.settings import get_restaurant_settings

		tip_item = get_restaurant_settings(restaurant).get("tip_item")
	tips = sum(flt(row.amount) for row in draft.items if tip_item and row.item_code == tip_item)
	return max(flt(draft.paid_amount) - tips, 0.0)


def get_provisional_ticket_markup(doc, paper_width=80, paid=0):
	"""Star Document Markup of the provisional ticket of `doc`, an order priced by ERPNext (build_unsaved_invoice)."""
	chars = LINE_CHARS.get(_width(paper_width), 48)
	rule = "-" * chars

	def money(value):
		return fmt_money(flt(value), currency=doc.currency).strip()

	now = now_datetime()
	out = [
		"[align: centre]",
		_big(doc.company, chars, height=1),
		"",
		_big(_("PROVISIONAL TICKET"), chars, bold=True),
		# The label of the table, or "Takeaway" and the order's number
		_big(_where(doc), chars),
		"[align: left]",
		"",
		f"{_('Date')}: {formatdate(now.date())} {now.strftime('%H:%M')}",
		rule,
	]
	for line in doc.items:
		offered = cint(line.get("posa_is_offered"))
		name = _cut(line.item_name or line.item_code, MAX_NAME_CHARS)
		_fit(out, f"{_qty(line.qty)} x {name}", money(0 if offered else line.amount), chars)
		remarks = _modifiers(line.get("posa_item_modifiers")) + _plain(line.get("posa_special_instructions"))
		for remark in remarks[:MAX_REMARK_ROWS]:
			out.append(f"   {_text(_cut(remark, MAX_REMARK_CHARS))}")
		if offered:
			out.append(f"   {_text(_('Offered', context='Given for free'))}")
	out.append(rule)

	# The totals as ERPNext worked them out, laid out as on the paid receipt (receipt_markup)
	if flt(doc.get("discount_amount")):
		_fit(out, f"{_('Discount')}:", money(doc.discount_amount), chars)
	if any(not tax.get("included_in_print_rate") for tax in charged_taxes(doc)):
		_fit(out, f"{_('Net Total')}:", money(doc.net_total), chars)
	for label, _base, amount in vat_lines(doc):
		_fit(out, f"{label}:", money(amount), chars)
	total = flt(doc.get("rounded_total")) or flt(doc.grand_total)
	if flt(doc.get("rounded_total")) and flt(doc.get("rounding_adjustment")):
		# The lines and the VAT add up to the grand total; the till charges it rounded to the 5 centimes
		_fit(out, f"{_('Rounding Adjustment')}:", money(doc.rounding_adjustment), chars)
	out.append(f"[magnify: width 1; height 2][bold: on]{_column(_('Total') + ':', money(total))}[bold: off][magnify]")
	if flt(paid):
		_fit(out, f"{_('Already paid')}:", money(paid), chars)
		out.append(f"[bold: on]{_column(_('Left to pay') + ':', money(max(total - flt(paid), 0)))}[bold: off]")
	out += [
		rule,
		"[align: centre]",
		f"[magnify: width 1; height 2][bold: on]{_text(_('THIS IS NOT A RECEIPT'))}[bold: off][magnify]",
		"[align: left]",
		"[cut: feed; partial]",
	]
	return "\n".join(out)


def _fit(out, left, right, chars):
	"""A line with an amount on the right; a text too long for it goes on its own line, its amount on the next."""
	left = printable(left)
	if len(left) + len(right) + 1 <= chars:
		out.append(_column(left, right))
	else:
		out += [_text(left), _column("", right)]


def _big(text, chars, height=2, bold=False):
	"""A heading twice as wide when it fits the paper (24 characters on 80 mm, 16 on 58 mm), else only twice as tall."""
	text = printable(text)
	width = 2 if len(text) * 2 <= chars else 1
	body = f"[bold: on]{_text(text)}[bold: off]" if bold else _text(text)
	return f"[magnify: width {width}; height {height}]{body}[magnify]"
