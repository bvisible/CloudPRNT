"""The till receipt of a POS sale, as Star Document Markup.

A sale from the POS is a Sales Invoice (is_pos) since the till moved to POS Next, a POS Invoice before; both
carry the same fields used here. Amounts sit in [column] tags, which cputil lays out for the printer's paper
width, so the same receipt fits a 58 mm and an 80 mm roll.
"""

import hashlib
import json
import os
import re

import frappe
from frappe import _
from frappe.utils import flt, fmt_money, formatdate
from PIL import Image

#: Characters a line holds in font A, by paper width (12-dot characters on a 203 dpi head).
LINE_CHARS = {58: 32, 80: 48, 112: 69}


def get_receipt_markup(doctype, name=None, paper_width=80):
	"""The receipt of `doctype` `name` (or of the document `doctype` itself, when a document is passed)."""
	doc = doctype if not isinstance(doctype, str) else frappe.get_doc(doctype, name)
	chars = LINE_CHARS.get(_width(paper_width), 48)
	rule = "-" * chars
	currency = doc.currency
	settings = frappe.db.get_value(
		"CloudPRNT Settings", None, ["header_logo_url", "footer_logo_url"], as_dict=True
	) or frappe._dict()

	def money(value):
		return fmt_money(flt(value), currency=currency).strip()

	lines = ["[align: centre]"]
	logo = settings.header_logo_url or company_logo_path(doc.company)
	if logo:
		lines += [f"[image: url {logo}; width 60%; min-width 48mm]", "[feed: length 1mm]"]
	lines.append(f"[magnify: width 2; height 1]{_text(doc.company)}[magnify]")
	for address in _company_addresses(doc.company):
		lines.append(_text(address))

	lines += ["[align: left]", ""]
	lines.append(f"{_('Invoice')}: {_text(doc.name)}")
	posted_at = str(doc.posting_time or "").split(".")[0]
	lines.append(f"{_('Date')}: {formatdate(doc.posting_date)} {posted_at}".rstrip())
	if doc.get("customer_name"):
		lines.append(f"{_('Customer')}: {_text(doc.customer_name)}")
	cashier = frappe.db.get_value("User", doc.owner, ["first_name", "last_name"], as_dict=True)
	if cashier:
		lines.append(f"{_('Cashier')}: {_text(cashier.first_name or '')} {_text((cashier.last_name or '')[:1])}".rstrip())
	lines.append(f"{_('Currency')}: {currency}")
	lines += ["", rule]

	for item in doc.items:
		label = item.item_name or item.item_code
		if item.item_name and not _same(item.item_code, item.item_name):
			label = f"{item.item_name} ({item.item_code})"
		lines.append(f"[bold: on]{_text(label)}[bold: off]")
		for serial, batch in _serials_and_batches(item):
			if serial:
				lines.append(f"{_('Serial number')}: {_text(serial)}")
			if batch:
				lines.append(f"{_('Batch No')}: {_text(batch)}")
		lines.append(_column(f"{_qty(item.qty)} x {money(item.rate)}", money(item.amount)))

	for code, value in _gift_cards_sold(doc):
		lines.append(f"{_('Gift card with a value of')} {money(value)}: {_text(code)}")
	lines += [rule, ""]

	if flt(doc.get("discount_amount")):
		lines.append(_column(f"{_('Discount')}:", money(doc.discount_amount)))
	taxes = charged_taxes(doc)
	if any(not tax.get("included_in_print_rate") for tax in taxes):
		lines.append(_column(f"{_('Net Total')}:", money(doc.net_total)))
	vat = vat_lines(doc)
	for label, _base, amount in vat:
		lines.append(_column(f"{label}:", money(amount)))
	lines.append(f"[bold: on]{_column(_('Grand Total') + ':', money(doc.grand_total))}[bold: off]")
	if flt(doc.get("rounded_total")) and flt(doc.rounded_total) != flt(doc.grand_total):
		lines.append(_column(f"{_('Rounded Total')}:", money(doc.rounded_total)))

	gift_card_codes = [row.get("code") for row in doc.get("payment_gift_card") or [] if row.get("code")]
	for payment in doc.get("payments") or []:
		if not flt(payment.amount):
			continue
		label = payment.mode_of_payment
		if gift_card_codes and _is_gift_card_mode(label):
			label = f"{label} {_('N°')}: {', '.join(gift_card_codes)}"
		lines.append(_column(f"{label}:", money(payment.amount)))
	if flt(doc.get("change_amount")):
		lines.append(_column(f"{_('Change Amount')}:", money(doc.change_amount)))

	lines += ["", "[feed: length 3mm]", f"{_('Total product')}: {_qty(doc.total_qty)}"]
	if taxes:
		tax_id = frappe.db.get_value("Company", doc.company, "tax_id")
		if tax_id:
			lines.append(f"{_('VAT number')}: {_text(tax_id)}")
		for label, base, amount in vat:
			# "VAT 8.1 %: 14.80 + 1.20 = 16.00", the base, the tax and the total it was charged on
			lines.append(f"{_text(label)}: {money(base)} + {money(amount)} = {money(base + amount)}")

	if doc.get("terms"):
		lines += ["", _text(frappe.utils.strip_html(doc.terms))]
	if settings.footer_logo_url:
		lines += ["[align: centre]", f"[image: url {settings.footer_logo_url}; width 40%; min-width 30mm]"]
	lines += [
		"",
		"[align: centre]",
		f"[barcode: type code128; data {_param(doc.name)}; height 15mm; module 2; hri]",
		"[align: left]",
		"[cut: feed; partial]",
	]
	return "\n".join(lines)


def get_pos_invoice_markup(invoice_name):
	"""The receipt of POS Invoice `invoice_name` (kept for callers written before Sales Invoice receipts)."""
	return get_receipt_markup("POS Invoice", invoice_name)


def company_logo_path(company):
	"""The company's logo (Company.company_logo) as a file the printer's renderer reads from this server, or None.

	Thermal paper prints black or nothing: a transparent logo would print its transparent pixels black, so the
	file is flattened on white once and kept, by the logo's content, in the site's private folder."""
	file_url = frappe.get_cached_value("Company", company, "company_logo") if company else None
	if not file_url:
		return None
	try:
		source = frappe.get_doc("File", {"file_url": file_url}).get_full_path()
		return flatten_for_paper(source, frappe.get_site_path("private", "cloudprnt"))
	except Exception:
		frappe.log_error("CloudPRNT: the company logo could not be prepared for the receipt", frappe.get_traceback())
		return None


def flatten_for_paper(source, folder):
	"""`source` image on a white ground, in greys, saved once in `folder` under a name taken from its content."""
	with open(source, "rb") as handle:
		digest = hashlib.sha1(handle.read()).hexdigest()[:16]
	target = os.path.abspath(os.path.join(folder, f"logo-{digest}.png"))
	if not os.path.exists(target):
		os.makedirs(folder, exist_ok=True)
		with Image.open(source) as image:
			image = image.convert("RGBA")
			flat = Image.new("RGB", image.size, (255, 255, 255))
			flat.paste(image, mask=image.getchannel("A"))
			flat.convert("L").save(target, "PNG")
	return target


def is_cash_sale(doc):
	"""Whether the sale moved cash: a payment (or a refund, on a return) by a mode of payment of type Cash, or
	change given."""
	if flt(doc.get("change_amount")):
		return True
	modes = {payment.mode_of_payment for payment in doc.get("payments") or [] if flt(payment.amount)}
	if not modes:
		return False
	return bool(frappe.get_all("Mode of Payment", filters={"name": ("in", list(modes)), "type": "Cash"}, limit=1))


def _width(paper_width):
	try:
		return round(float(str(paper_width or 80).replace("mm", "")))
	except ValueError:
		return 80


#: A line break, a tab, a vertical tab, a form feed, the Unicode line and paragraph separators: read as a space.
_AS_A_SPACE = re.compile("[\t\n\r\x0b\x0c\u2028\u2029]")
#: What a printer must never be sent as text a person typed: the other C0 controls (BEL and SUB drive the cash drawer
#: in Star line mode, ESC @ resets the printer, NUL ends a string), DEL, the C1 controls, the right-to-left and other
#: bidirectional overrides and isolates (they reorder what the paper reads), the zero-width controls and the byte order mark.
_UNSAFE = re.compile("[\x00-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]")


def printable(value):
	"""`value` as text a printer may be sent, the ONE function every text of a ticket goes through (`_text`, `_param`,
	so `_column`, in the receipt, the provisional ticket and the kitchen ticket).

	A guest's remark is stored as typed and a waiter's note too, and a dish's name is a person's: none may carry a
	character that drives the printer or the reader's eye. A line break or a tab reads as a space, so that two words
	never run together; every other control character is removed."""
	return _UNSAFE.sub("", _AS_A_SPACE.sub(" ", str(value or "")))


def _text(value):
	"""Text printed as it is: made safe (`printable`), the markup's own characters escaped."""
	return printable(value).replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _param(value):
	"""Text inside a tag, where ';' ends the value and brackets end the tag (cputil escapes neither there).

	A backslash is cputil's escape there too: "AB\\" prints "AB\", and a lone "AB\" before the ';' is eaten (seen with
	cputil 2.0.1, 31.10.2026). It is doubled, so a remark ending with a backslash keeps it and keeps the column."""
	return printable(value).replace("\\", "\\\\").replace(";", ",").replace("[", "(").replace("]", ")")


def _column(left, right):
	return f"[column: left {_param(left)}; right {_param(right)}]"


def _qty(value):
	value = flt(value)
	return str(int(value)) if value == int(value) else f"{value:g}"


def _same(code, name):
	def squash(text):
		return "".join(ch for ch in (text or "").lower() if ch not in " -")

	return squash(code) == squash(name)


def _company_addresses(company):
	lines = []
	for address in frappe.get_all(
		"Address",
		filters={"is_your_company_address": 1, "link_doctype": "Company", "link_name": company},
		fields=["address_line1", "address_line2", "city", "pincode"],
	):
		parts = [address.address_line1, address.address_line2, " ".join(filter(None, [address.pincode, address.city]))]
		lines.append(" ".join(part for part in parts if part))
	return lines


def _serials_and_batches(item):
	bundle = item.get("serial_and_batch_bundle")
	if not bundle:
		return []
	try:
		return frappe.get_all(
			"Serial and Batch Entry", filters={"parent": bundle}, fields=["serial_no", "batch_no"], as_list=True
		)
	except Exception:
		return []


def _gift_cards_sold(doc):
	"""Gift cards sold on this receipt: Coupon Codes of type Gift Card created from it (their description
	names the invoice)."""
	if not any(item.item_code == "giftcard" for item in doc.items):
		return []
	try:
		return frappe.get_all(
			"Coupon Code",
			filters={"coupon_type": "Gift Card", "description": ["like", f"%{doc.name}%"]},
			fields=["coupon_code", "gift_card_amount"],
			as_list=True,
		)
	except Exception:
		return []


def _is_gift_card_mode(mode_of_payment):
	# Gift card payments carry their codes in payment_gift_card; the mode of payment they are paid with is the
	# one named after gift cards on the sites that sell them.
	name = (mode_of_payment or "").lower()
	return "cadeau" in name or "gift" in name


def _tax_label(tax, abbr):
	"""A tax row's label without the company abbreviation and the account number ("2200 - VAT 8.1% - AB")."""
	label = (tax.get("description") or tax.get("account_head") or "").strip()
	if abbr and label.endswith(f" - {abbr}"):
		label = label[: -len(f" - {abbr}")]
	head, sep, rest = label.partition(" - ")
	if sep and head.strip().isdigit():
		label = rest
	return label.strip()


def tax_owed(tax):
	"""The VAT a tax row charges, after the order's discount. ERPNext keeps `tax_amount` as it was before a discount on
	the grand total, and the amount owed in `tax_amount_after_discount_amount`."""
	owed = tax.get("tax_amount_after_discount_amount")
	return flt(owed) if owed is not None else flt(tax.get("tax_amount"))


def charged_taxes(doc):
	"""The tax rows of `doc` that charge something (a Swiss template has a row for every rate, most at 0)."""
	return [tax for tax in doc.get("taxes") or [] if flt(tax_owed(tax))]


def vat_lines(doc):
	"""The VAT of `doc`, one line per effective rate, the highest first: [(label, base, amount)] (ruling 96).

	The label is « VAT 8.1 % », made from the rate ERPNext charged, never from the tax row's name (the account's, « TVA
	8.1% - Due »). A Swiss till's template has one row per VAT account at rate 0, each item's rate coming from its item
	tax template: the rate is read in the row's per-item detail (`item_wise_tax_detail`), else from the row's own rate.
	A row whose items carry two rates is split between them in proportion to the VAT each was charged. The amount is
	the VAT owed (tax_owed), the base the net amount of the items charged at that rate, both after the order's
	discount; rows at the same rate are added up. A row whose rate cannot be read (no per-item detail, no rate of its
	own, a fixed amount) gets a line of its own under its name, as before."""
	precision = 2
	by_rate, unread = {}, []
	for tax in charged_taxes(doc):
		owed = tax_owed(tax)
		rates = _rates_charged(tax)
		if not rates and flt(tax.get("rate")):
			rates = {flt(tax.rate): (owed, None)}
		if not rates:
			unread.append((_tax_label(tax, _abbr(doc)), flt(doc.get("net_total")), owed))
			continue
		weights = sum(weight for weight, _codes in rates.values()) or 1
		left = owed
		for index, (rate, (weight, codes)) in enumerate(sorted(rates.items(), reverse=True)):
			share = left if index == len(rates) - 1 else flt(owed * weight / weights, precision)
			left = flt(left - share, precision)
			base = flt(doc.get("net_total")) if codes is None else _net_of(doc, codes)
			line = by_rate.setdefault(rate, [0.0, 0.0])
			line[0] += base
			line[1] += share
	# After an order discount the lines' net amounts can lose a centime to rounding: the net the totals say (the grand
	# total less the VAT owed) is then a centime more than their sum. The largest base takes it, so that each base and
	# its VAT add up to the total (« 74.01 + 5.99 = 80.00 », not « 74.00 + 5.99 = 79.99 »)
	items = doc.get("items") or []
	net = flt(doc.get("grand_total")) - sum(tax_owed(tax) for tax in charged_taxes(doc))
	lost = flt(net - sum(flt(item.get("net_amount")) for item in items), precision)
	# a centime or so per line, never more: anything larger is not a rounding and is left alone
	if lost and by_rate and abs(lost) <= 0.01 * max(len(items), 1):
		largest = max(by_rate, key=lambda rate: by_rate[rate][0])
		by_rate[largest][0] += lost
	lines = [
		(_("VAT {0} %").format(_rate_text(rate)), flt(base, precision), flt(amount, precision))
		for rate, (base, amount) in sorted(by_rate.items(), reverse=True)
	]
	return lines + unread


def _rates_charged(tax):
	"""{rate: (VAT charged at it, {item codes})} from a tax row's per-item detail; {} when it has none."""
	try:
		detail = json.loads(tax.get("item_wise_tax_detail") or "{}")
	except ValueError:
		return {}
	rates = {}
	for item_code, value in (detail.items() if isinstance(detail, dict) else []):
		if isinstance(value, list):
			rate, amount = (value + [0, 0])[:2]
		elif isinstance(value, dict):
			rate, amount = value.get("tax_rate"), value.get("tax_amount")
		else:
			rate, amount = value, 0
		if not flt(rate):
			continue
		weight, codes = rates.get(flt(rate), (0.0, set()))
		codes.add(item_code)
		rates[flt(rate)] = (weight + abs(flt(amount)), codes)
	return rates


def _net_of(doc, item_codes):
	return sum(flt(item.get("net_amount")) for item in doc.get("items") or [] if item.get("item_code") in item_codes)


def _rate_text(rate):
	"""8.1 → "8.1", 8.0 → "8": the rate as a person writes it."""
	return f"{flt(rate):g}"


def _abbr(doc):
	return frappe.get_cached_value("Company", doc.company, "abbr") or "" if doc.get("company") else ""
