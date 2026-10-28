"""The till receipt of a POS sale, as Star Document Markup.

A sale from the POS is a Sales Invoice (is_pos) since the till moved to POS Next, a POS Invoice before; both
carry the same fields used here. Amounts sit in [column] tags, which cputil lays out for the printer's paper
width, so the same receipt fits a 58 mm and an 80 mm roll.
"""

import json

import frappe
from frappe import _
from frappe.utils import flt, fmt_money, formatdate

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
	if settings.header_logo_url:
		lines += [f"[image: url {settings.header_logo_url}; width 60%; min-width 48mm]", "[feed: length 1mm]"]
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
	taxes = [tax for tax in doc.get("taxes") or [] if flt(tax.tax_amount)]
	if any(not tax.get("included_in_print_rate") for tax in taxes):
		lines.append(_column(f"{_('Net Total')}:", money(doc.net_total)))
	abbr = frappe.get_cached_value("Company", doc.company, "abbr") or ""
	for tax in taxes:
		lines.append(_column(f"{_tax_label(tax, abbr)}:", money(tax.tax_amount)))
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
		for tax, base in _tax_bases(doc, taxes):
			amount = flt(tax.get("base_tax_amount_after_discount_amount") or tax.tax_amount)
			# "VAT 8.1%: 14.80 + 1.20 = 16.00", the base, the tax and the total it was charged on
			lines.append(f"{_tax_label(tax, abbr)}: {money(base)} + {money(amount)} = {money(base + amount)}")

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


def _text(value):
	"""Text printed as it is: markup's own characters escaped."""
	return str(value or "").replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]").replace("\n", " ")


def _param(value):
	"""Text inside a tag, where ';' ends the value and brackets end the tag (cputil escapes neither there)."""
	return str(value or "").replace(";", ",").replace("[", "(").replace("]", ")").replace("\n", " ")


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


def _tax_bases(doc, taxes):
	"""Each tax row with the net amount it was charged on, from the per-item detail ERPNext keeps on it."""
	for tax in taxes:
		try:
			detail = json.loads(tax.get("item_wise_tax_detail") or "{}")
		except ValueError:
			detail = {}
		taxed = set()
		for item_code, value in detail.items():
			if isinstance(value, list):
				rate = value[0] if value else 0
			elif isinstance(value, dict):
				rate = value.get("tax_rate")
			else:
				rate = value
			if flt(rate):
				taxed.add(item_code)
		if detail:
			base = sum(flt(item.net_amount) for item in doc.items if item.item_code in taxed)
		else:
			base = flt(doc.net_total)
		yield tax, base
