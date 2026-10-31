"""The till receipt, laid out as Star Document Markup from a POS sale."""

import os
import tempfile
from types import SimpleNamespace

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import fmt_money

from PIL import Image

from cloudprnt.receipt_markup import _column, _param, _text, flatten_for_paper, get_receipt_markup, printable
from cloudprnt.tests.hostile_text import CLEAN, HOSTILE, unsafe_characters


class Sale(SimpleNamespace):
	"""A sale as the receipt reads it: attributes and .get(), like a Document (a dict's own .items would hide the
	items table)."""

	def get(self, key, default=None):
		return getattr(self, key, default)


def chf(value):
	"""An amount as the site prints it (with or without its symbol, as the site is set)."""
	return fmt_money(value, currency="CHF").strip()


def sale(**overrides):
	row = frappe._dict
	doc = Sale(
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
				# the company does not exist here, so its abbreviation is unknown: the description carries none
				description="2200 - VAT 8.1%",
				account_head="2200 - VAT 8.1% - NSC",
				tax_amount=1.5,
				base_tax_amount_after_discount_amount=1.5,
				included_in_print_rate=1,
				item_wise_tax_detail='{"ESP": [8.1, 0.67], "croissant": [8.1, 0.83]}',
			)
		],
		payments=[row(mode_of_payment="Cash", amount=20.0)],
	)
	doc.__dict__.update(overrides)
	return doc


class TestReceiptMarkup(FrappeTestCase):
	def test_amounts_sit_in_columns_and_text_is_escaped(self):
		markup = get_receipt_markup(sale())
		self.assertIn("Espresso \\[double\\] (ESP)", markup, "brackets of a name are printed, not read as tags")
		self.assertIn(f"[column: left 2 x {chf(4.5)}; right {chf(9)}]", markup)
		self.assertIn(f"[column: left Cash:; right {chf(20)}]", markup)
		self.assertIn("Croissant", markup)
		self.assertNotIn("Croissant (croissant)", markup, "a code that only differs in case is not repeated")
		self.assertIn("[barcode: type code128; data ACC-SINV-TEST-0001;", markup)
		self.assertTrue(markup.rstrip().endswith("[cut: feed; partial]"))

	def test_taxes_show_their_label_and_base(self):
		markup = get_receipt_markup(sale())
		vat = frappe._("VAT {0} %").format("8.1")
		# The label is made from the rate charged (ruling 96), not from the tax row's description
		self.assertIn(f"[column: left {vat}:; right {chf(1.5)}]", markup)
		self.assertNotIn("2200 - VAT", markup, "the account number is dropped")
		self.assertNotIn("VAT 8.1%", markup, "the row's own description is not the label")
		self.assertNotIn(frappe._("Net Total"), markup, "prices include the tax: no net total line")
		self.assertIn(f"{vat}: {chf(18.5)} + {chf(1.5)} = {chf(20)}", markup, "the base is the net amount of the taxed items")

	def test_a_tax_added_on_top_shows_the_net_total(self):
		doc = sale()
		doc.taxes[0].included_in_print_rate = 0
		self.assertIn(frappe._("Net Total"), get_receipt_markup(doc))

	def test_the_rule_fits_the_paper(self):
		self.assertIn("-" * 32 + "\n", get_receipt_markup(sale(), paper_width="58mm"))
		self.assertNotIn("-" * 33, get_receipt_markup(sale(), paper_width="58mm"))
		self.assertIn("-" * 48, get_receipt_markup(sale(), paper_width=80))


class TestWhatNeverReachesThePrinter(FrappeTestCase):
	"""A guest's QR remark is stored as typed, a waiter's note too: the markup is the last place to make them safe."""

	def test_every_control_character_is_removed_by_the_one_shared_function(self):
		controls = [*range(0x00, 0x20), *range(0x7F, 0xA0), *range(0x200B, 0x2010), 0x2028, 0x2029]
		controls += [*range(0x202A, 0x202F), *range(0x2066, 0x206A), 0xFEFF]
		for helper in (printable, _text, _param):
			survivors = [hex(code) for code in controls if unsafe_characters(helper(f"a{chr(code)}b"))]
			self.assertEqual(survivors, [], helper.__name__)
			self.assertEqual(helper(HOSTILE), CLEAN, helper.__name__)

	def test_a_line_break_or_a_tab_reads_as_a_space_and_the_other_controls_vanish(self):
		for text in ("a\nb", "a\rb", "a\tb", "a\x0bb", "a\x0cb", "a\r\nb"):
			with self.subTest(text=repr(text)):
				self.assertEqual(printable(text).replace("  ", " "), "a b")
		self.assertEqual(printable("a\x07b\x1ab\x1b@c\x00"), "abb@c")
		self.assertEqual(printable(None), "")
		self.assertEqual(printable("  kept  "), "  kept  ")

	def test_the_markups_own_characters_are_still_escaped_after_the_cleaning(self):
		self.assertEqual(_text("a[b]\\c"), "a\\[b\\]\\\\c")
		self.assertEqual(_param("a;b[c]\\"), "a,b(c)\\\\")

	def test_a_column_holds_no_control_character_in_either_side(self):
		self.assertEqual(unsafe_characters(_column(HOSTILE, HOSTILE)), [])

	def test_the_paid_receipt_prints_no_control_character_whatever_the_names_hold(self):
		row = frappe._dict
		doc = sale(
			company=HOSTILE,
			customer_name=HOSTILE,
			terms=HOSTILE,
			items=[row(item_code="ESP", item_name=HOSTILE, qty=1, rate=4.5, amount=4.5, net_amount=4.5)],
			payments=[row(mode_of_payment=HOSTILE, amount=4.5)],
		)
		markup = get_receipt_markup(doc)
		self.assertEqual(unsafe_characters(markup), [])
		self.assertIn(f"{frappe._('Customer')}: {CLEAN}", markup)
		self.assertIn(f"[bold: on]{CLEAN} (ESP)[bold: off]", markup)
		self.assertIn(f"[magnify: width 2; height 1]{CLEAN}[magnify]", markup)


class TestLogoOnPaper(FrappeTestCase):
	def test_a_transparent_logo_prints_on_white(self):
		with tempfile.TemporaryDirectory() as folder:
			source = os.path.join(folder, "logo.png")
			logo = Image.new("RGBA", (4, 2), (0, 0, 0, 0))
			logo.putpixel((0, 0), (20, 20, 20, 255))
			logo.save(source)
			target = flatten_for_paper(source, os.path.join(folder, "out"))
			with Image.open(target) as flat:
				self.assertEqual(flat.mode, "L")
				self.assertEqual(flat.getpixel((3, 1)), 255, "a transparent pixel turns white, not black")
				self.assertLess(flat.getpixel((0, 0)), 40, "the drawing stays dark")
			self.assertEqual(flatten_for_paper(source, os.path.join(folder, "out")), target, "made once, then reused")
