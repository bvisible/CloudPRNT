"""The till receipt, laid out as Star Document Markup from a POS sale."""

import frappe
from frappe.tests.utils import FrappeTestCase

from cloudprnt.receipt_markup import get_receipt_markup


def sale(**overrides):
	row = frappe._dict
	doc = row(
		doctype="Sales Invoice",
		name="ACC-SINV-TEST-0001",
		company="_No Such Company [test]",
		currency="CHF",
		posting_date="2026-10-28",
		posting_time="14:05:09.123456",
		customer_name="Walk-in; customer",
		owner="Administrator",
		discount_amount=0,
		net_total=18.5,
		grand_total=20.0,
		rounded_total=20.0,
		change_amount=0,
		total_qty=3,
		terms="",
		items=[
			row(item_code="ESP", item_name="Espresso [double]", qty=2, rate=4.5, amount=9.0, net_amount=8.33),
			row(item_code="croissant", item_name="Croissant", qty=1, rate=11.0, amount=11.0, net_amount=10.17),
		],
		taxes=[
			row(
				description="2200 - VAT 8.1% - NSC",
				account_head="2200 - VAT 8.1% - NSC",
				tax_amount=1.5,
				base_tax_amount_after_discount_amount=1.5,
				included_in_print_rate=1,
				item_wise_tax_detail='{"ESP": [8.1, 0.67], "croissant": [8.1, 0.83]}',
			)
		],
		payments=[row(mode_of_payment="Cash", amount=20.0)],
	)
	doc.update(overrides)
	return doc


class TestReceiptMarkup(FrappeTestCase):
	def test_amounts_sit_in_columns_and_text_is_escaped(self):
		markup = get_receipt_markup(sale())
		self.assertIn("Espresso \\[double\\] (ESP)", markup, "brackets of a name are printed, not read as tags")
		self.assertIn("[column: left 2 x CHF 4.50; right CHF 9.00]", markup)
		self.assertIn("[column: left Cash:; right CHF 20.00]", markup)
		self.assertIn("Croissant", markup)
		self.assertNotIn("Croissant (croissant)", markup, "a code that only differs in case is not repeated")
		self.assertIn("[barcode: type code128; data ACC-SINV-TEST-0001;", markup)
		self.assertTrue(markup.rstrip().endswith("[cut: feed; partial]"))

	def test_taxes_show_their_label_and_base(self):
		markup = get_receipt_markup(sale())
		self.assertIn("VAT 8.1%", markup)
		self.assertNotIn("2200 - VAT", markup, "the account number is dropped")
		self.assertNotIn(frappe._("Net Total"), markup, "prices include the tax: no net total line")
		self.assertIn("CHF 18.50", markup, "the tax base is the net amount of the taxed items")

	def test_a_tax_added_on_top_shows_the_net_total(self):
		doc = sale()
		doc.taxes[0].included_in_print_rate = 0
		self.assertIn(frappe._("Net Total"), get_receipt_markup(doc))

	def test_the_rule_fits_the_paper(self):
		self.assertIn("-" * 32 + "\n", get_receipt_markup(sale(), paper_width="58mm"))
		self.assertNotIn("-" * 33, get_receipt_markup(sale(), paper_width="58mm"))
		self.assertIn("-" * 48, get_receipt_markup(sale(), paper_width=80))
