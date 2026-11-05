"""The provisional ticket of a table order: the order as the till shows it, priced by ERPNext, printed without saving.

Two kinds of tests. The layout of the ticket, from an order written out by hand (no site data). And the ticket of a
real order: the till's payload, shaped as its payment sends it, priced by the REAL ERPNext computation on a till, its
taxes, items and restaurant that each test builds and rolls back (ruling 93: no fake server works out a total).
Those need POS Next and a company with its books: they skip without them.
"""

import json
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, fmt_money, formatdate, nowdate

from cloudprnt import drawer, provisional_ticket
from cloudprnt import print_queue_manager as jobs
from cloudprnt.printers import PRINTER, SETTINGS
from cloudprnt.provisional_ticket import (
	NoTicketPrinter,
	get_provisional_ticket_markup,
	print_provisional_ticket,
	ticket_status,
)
from cloudprnt.receipt_markup import get_receipt_markup
from cloudprnt.tests.hostile_text import CLEAN, HOSTILE, unsafe_characters

T = "_Test Prov"
PORTAL = "cloudprnt-provisional-portal@yopmail.com"
PORTAL_WITH_ROLES = "cloudprnt-provisional-portal-roles@yopmail.com"
DESK_WITHOUT_INVOICES = "cloudprnt-provisional-desk@yopmail.com"
CASHIER = "cloudprnt-provisional-cashier@yopmail.com"
READER = "cloudprnt-provisional-reader@yopmail.com"
MANAGER = "cloudprnt-provisional-manager@yopmail.com"
# POS Next names a new table by a hash, and keeps what a person reads in its label
TABLE_NAME = "k3x9q2m7ab"
LABELS = {TABLE_NAME: "Table 5"}


def chf(value):
	"""An amount as the site prints it (with or without its symbol, as the site is set)."""
	return fmt_money(value, currency="CHF").strip()


def vat(rate):
	return frappe._("VAT {0} %").format(rate)


def read_labels(doctype, label_field, name):
	return LABELS.get(name, name)


@contextmanager
def as_user(user):
	frappe.set_user(user)
	try:
		yield
	finally:
		frappe.set_user("Administrator")


def make_user(email, roles, user_type):
	"""A user of the test's own with `roles`; `user_type` is written after the insert, as a portal account that was
	given desk roles by mistake keeps its type."""
	if frappe.db.exists("User", email):
		frappe.delete_doc("User", email, ignore_permissions=True, force=True)
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": email.split("@")[0],
			"send_welcome_email": 0,
			"roles": [{"role": role} for role in roles if frappe.db.exists("Role", role)],
		}
	)
	user.flags.skip_drive_setup = True
	user.insert(ignore_permissions=True)
	frappe.db.set_value("User", email, "user_type", user_type)
	frappe.clear_cache(user=email)
	return email


class Order(SimpleNamespace):
	"""An order written out by hand: attributes and .get(), like a Document (a dict's own .items would hide the
	items table)."""

	def get(self, key, default=None):
		return getattr(self, key, default)


def written_order(**overrides):
	row = frappe._dict
	doc = Order(
		doctype="Sales Invoice",
		name=None,
		company="_No Such Company [test]",
		currency="CHF",
		restaurant_table=TABLE_NAME,
		is_takeaway=0,
		takeaway_number="",
		discount_amount=0,
		net_total=107.88,
		grand_total=116.03,
		rounded_total=116.05,
		rounding_adjustment=0.02,
		items=[
			row(
				item_code="STEAK",
				item_name="Steak [rare]",
				qty=2,
				amount=77.0,
				net_amount=71.23,
				posa_item_modifiers='[{"group": "Cooking", "options": [{"name": "Medium", "price_adjustment": 0}]}]',
				posa_special_instructions="No salt; please\\",
			),
			row(item_code="BREAD", item_name="Bread to take away", qty=1.5, amount=31.03, net_amount=30.24),
			row(item_code="WINE", item_name="House red", qty=1, amount=8.0, net_amount=7.40),
			row(item_code="TIRAMISU", item_name="Tiramisu", qty=1, amount=0, net_amount=0, posa_is_offered=1),
		],
		taxes=[
			# a Swiss till's template: one row per VAT account at rate 0, the rate coming from each item
			row(
				description="TVA 8.1% - Due",
				rate=0,
				tax_amount=6.37,
				tax_amount_after_discount_amount=6.37,
				included_in_print_rate=1,
				item_wise_tax_detail='{"STEAK": [8.1, 5.77], "WINE": [8.1, 0.60], "BREAD": [0, 0]}',
			),
			row(
				description="TVA 2.6% - Due",
				rate=0,
				tax_amount=0.79,
				tax_amount_after_discount_amount=0.79,
				included_in_print_rate=1,
				item_wise_tax_detail='{"STEAK": [0, 0], "WINE": [0, 0], "BREAD": [2.6, 0.79]}',
			),
			row(
				description="TVA 3.8% - Due",
				rate=0,
				tax_amount=0,
				tax_amount_after_discount_amount=0,
				included_in_print_rate=1,
				item_wise_tax_detail="{}",
			),
		],
		payments=[row(mode_of_payment="Cash", amount=20.0)],
	)
	doc.__dict__.update(overrides)
	return doc


class RolledBack(FrappeTestCase):
	"""Whatever a test writes is rolled back: nothing of it stays on the site."""

	permissions_changed = False

	def forget_cached_permissions(self):
		"""The rollback does not reach the caches: a test that wrote permission rules clears them once it is rolled back."""
		if self.permissions_changed:
			frappe.clear_cache()

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.addCleanup(frappe.set_user, "Administrator")
		self.addCleanup(self.forget_cached_permissions)
		self.addCleanup(frappe.db.rollback)
		commit = patch.object(frappe.db, "commit")
		commit.start()
		self.addCleanup(commit.stop)


class TestTheTicketsLayout(FrappeTestCase):
	def markup(self, doc=None, **options):
		with patch("cloudprnt.labels.label_of", read_labels):
			return get_provisional_ticket_markup(doc or written_order(), **options)

	def test_the_ticket_shows_the_lines_the_vat_of_each_rate_the_rounding_and_the_total(self):
		markup = self.markup()
		self.assertIn("_No Such Company \\[test\\]", markup, "the brackets of a name are printed, not read as tags")
		self.assertIn(frappe._("PROVISIONAL TICKET"), markup)
		self.assertIn("Table 5", markup, "the table is named by its label")
		self.assertNotIn(TABLE_NAME, markup)
		self.assertIn(f"{frappe._('Date')}: {formatdate(nowdate())}", markup)
		self.assertIn(f"[column: left 2 x Steak (rare); right {chf(77)}]", markup)
		self.assertNotIn("[rare]", markup, "a name never opens a tag")
		self.assertIn("   Cooking: Medium", markup)
		self.assertIn(f"[column: left 1.5 x Bread to take away; right {chf(31.03)}]", markup)
		# one line per effective rate, labelled by its rate (ruling 96), never by the account's name
		self.assertIn(f"[column: left {vat('8.1')}:; right {chf(6.37)}]", markup)
		self.assertIn(f"[column: left {vat('2.6')}:; right {chf(0.79)}]", markup)
		self.assertLess(markup.index(vat("8.1")), markup.index(vat("2.6")), "the highest rate first")
		self.assertNotIn("Due", markup)
		self.assertNotIn("3.8", markup, "a rate that charged nothing is not printed")
		self.assertNotIn(frappe._("Net Total"), markup, "prices include the tax: no net total line")
		# the lines and the VAT come to 116.03: the rounding to the 5 centimes is said (M1)
		self.assertIn(f"[column: left {frappe._('Rounding Adjustment')}:; right {chf(0.02)}]", markup)
		self.assertIn(f"[column: left {frappe._('Total')}:; right {chf(116.05)}]", markup)
		self.assertIn(frappe._("THIS IS NOT A RECEIPT"), markup)
		self.assertNotIn("Cash", markup, "no payment of the till on a provisional ticket")
		self.assertNotIn("[barcode", markup, "no invoice barcode: it is not the invoice")
		self.assertTrue(markup.rstrip().endswith("[cut: feed; partial]"))

	def test_an_offered_row_is_said_offered_and_charges_nothing(self):
		markup = self.markup()
		self.assertIn(f"[column: left 1 x Tiramisu; right {chf(0)}]", markup)
		self.assertIn(f"   {frappe._('Offered', context='Given for free')}", markup)

	def test_what_a_guest_paid_by_phone_is_said_with_what_is_left_to_pay(self):
		markup = self.markup(paid=20)
		self.assertIn(f"[column: left {frappe._('Already paid')}:; right {chf(20)}]", markup)
		self.assertIn(f"[column: left {frappe._('Left to pay')}:; right {chf(96.05)}]", markup)
		self.assertNotIn(frappe._("Already paid"), self.markup(), "nothing paid, nothing said")

	def test_a_remark_ending_with_a_backslash_or_holding_a_semicolon_keeps_its_line(self):
		markup = self.markup()
		# in text a backslash is cputil's escape: doubled, it prints once; ';' is plain text outside a tag
		self.assertIn("   No salt; please\\\\\n", markup)
		doc = written_order()
		doc.items[0].item_name = "Steak\\"
		self.assertIn(f"[column: left 2 x Steak\\\\; right {chf(77)}]", self.markup(doc), "a value never escapes ';'")
		doc.items[0].item_name = "Steak; well\rdone"
		self.assertIn(f"[column: left 2 x Steak, well done; right {chf(77)}]", self.markup(doc))

	def test_no_control_character_reaches_the_printer_whatever_the_names_and_remarks_hold(self):
		# A guest's QR remark is stored as typed. BEL and SUB drive the cash drawer in Star line mode, ESC @ resets the
		# printer: the ticket is as safe as the receipt, in an item name, an option, a remark and the table's label.
		doc = written_order()
		doc.items[0].item_name = HOSTILE
		doc.items[0].posa_special_instructions = HOSTILE
		doc.items[0].posa_item_modifiers = json.dumps([{"group": HOSTILE, "options": [{"name": HOSTILE}]}])
		with patch("cloudprnt.labels.label_of", lambda doctype, field, name: HOSTILE if name == TABLE_NAME else name):
			markup = get_provisional_ticket_markup(doc)
		self.assertEqual(unsafe_characters(markup), [])
		self.assertIn(f"[column: left 2 x {CLEAN}; right {chf(77)}]", markup)
		self.assertIn(f"   {CLEAN}: {CLEAN}", markup, "an option group and its option")
		for row in ("ABC@DE", "F GHIJK", "LM"):
			self.assertIn(f"\n   {row}\n", markup, "a remark is read line by line, then made safe")
		self.assertIn(f"[magnify: width 2; height 2]{CLEAN}[magnify]", markup, "the table")
		# the company, and the number of a takeaway order
		doc = written_order(company=HOSTILE, restaurant_table=None, is_takeaway=1, takeaway_number=HOSTILE)
		markup = self.markup(doc)
		self.assertEqual(unsafe_characters(markup), [])
		self.assertIn(f"{frappe._('Takeaway')} {CLEAN}", markup)

	def test_a_line_break_in_a_name_does_not_start_another_line_of_the_ticket(self):
		doc = written_order()
		doc.items[0].item_name = "Steak\nfrites\n[cut: full]"
		markup = self.markup(doc)
		self.assertIn(f"[column: left 2 x Steak frites (cut: full); right {chf(77)}]", markup)
		self.assertNotIn("\n[cut: full]", markup)

	def test_a_takeaway_order_is_named_by_its_number(self):
		markup = self.markup(written_order(restaurant_table=None, is_takeaway=1, takeaway_number="T-012"))
		self.assertIn(f"{frappe._('Takeaway')} T-012", markup)

	def test_a_tax_added_on_top_shows_the_net_total(self):
		doc = written_order(discount_amount=5.0)
		doc.taxes[0].included_in_print_rate = 0
		markup = self.markup(doc)
		self.assertIn(f"[column: left {frappe._('Discount')}:; right {chf(5)}]", markup)
		self.assertIn(f"[column: left {frappe._('Net Total')}:; right {chf(107.88)}]", markup)

	def test_every_line_fits_the_paper_58_mm_included(self):
		for paper, chars in (("58mm", 32), (80, 48)):
			markup = self.markup(written_order(company="A restaurant with a long name"), paper_width=paper, paid=20)
			for line in markup.splitlines():
				with self.subTest(paper=paper, line=line):
					self.assertLessEqual(printed_width(line), chars)


def printed_width(line):
	"""How many characters a markup line takes on paper (a magnified line counts its width, a column both sides)."""
	import re

	column = re.search(r"\[column: left ([^;]*); right ([^\]]*)\]", line)
	if column:
		left, right = column.groups()
		return len(left) + len(right) + 1
	width = re.search(r"\[magnify: width (\d)", line)
	text = re.sub(r"\[[a-z]+[^\]]*\]", "", line).replace("\\\\", "\\").replace("\\[", "[").replace("\\]", "]")
	if line.startswith("[cut") or line.startswith("[align") or line.startswith("[feed"):
		return 0
	if text.strip().startswith(("1.5 x", "2 x", "1 x")):
		return 0  # a dish's name: the printer wraps it, its amount sits on the next line when it is long
	return len(text) * (int(width.group(1)) if width else 1)


class RealTill(RolledBack):
	"""A till of the test's own, priced by the real ERPNext: its company's books, two VAT accounts, the item tax templates
	of 8.1 % and 2.6 % (and one at 0 %), a template of rows at rate 0 as a Swiss till has (prices with the VAT in them),
	the same without (prices before tax), items, a restaurant with a table, and printers."""

	def setUp(self):
		super().setUp()
		if not provisional_ticket._restaurants_known():
			raise unittest.SkipTest("POS Next with restaurants is not installed on this site")
		company = frappe.db.get_value("Company", {}, "name", order_by="creation")
		group = frappe.db.get_value(
			"Account", {"company": company, "is_group": 1, "root_type": "Liability"}, "name", order_by="lft desc"
		)
		warehouse = frappe.db.get_value("Warehouse", {"company": company, "is_group": 0, "disabled": 0}, "name")
		mode = frappe.db.get_value("Mode of Payment Account", {"company": company}, "parent")
		price_list = frappe.db.get_value("Price List", {"selling": 1, "enabled": 1}, "name")
		if not (company and group and warehouse and mode and price_list):
			raise unittest.SkipTest("The site has no company with books, warehouse, payment mode and price list")
		self.company = company
		frappe.cache().delete_keys("cloudprnt_")
		self.vat81 = self.insert("Account", account_name=f"{T} VAT 8.1", parent_account=group, company=company, account_type="Tax", tax_rate=8.1)
		self.vat26 = self.insert("Account", account_name=f"{T} VAT 2.6", parent_account=group, company=company, account_type="Tax", tax_rate=2.6)

		def item_tax(title, r81, r26):
			return self.insert(
				"Item Tax Template",
				title=f"{T} {title}",
				company=company,
				taxes=[{"tax_type": self.vat81, "tax_rate": r81}, {"tax_type": self.vat26, "tax_rate": r26}],
			)

		self.itt81, self.itt26, self.itt0 = item_tax("8.1", 8.1, 0), item_tax("2.6", 0, 2.6), item_tax("none", 0, 0)

		def template(title, included):
			return self.insert(
				"Sales Taxes and Charges Template",
				title=f"{T} {title}",
				company=company,
				taxes=[
					{"charge_type": "On Net Total", "account_head": account, "description": f"{label} - Due", "rate": 0, "included_in_print_rate": included}
					for account, label in ((self.vat81, "TVA 8.1%"), (self.vat26, "TVA 2.6%"))
				],
			)

		incl, excl = template("incl", 1), template("excl", 0)

		def item(code, itt):
			return self.insert(
				"Item",
				item_code=f"{T} {code}",
				item_name=f"{T} {code}",
				item_group=frappe.db.get_value("Item Group", {"is_group": 0}, "name", order_by="lft"),
				stock_uom=frappe.db.get_value("UOM", "Nos") or frappe.db.get_value("UOM", {}, "name"),
				is_stock_item=0,
				is_sales_item=1,
				taxes=[{"item_tax_template": itt}] if itt else [],
				item_defaults=[{"company": company, "default_warehouse": warehouse}],
			)

		self.steak, self.wine, self.food = item("Steak", self.itt81), item("Wine", self.itt81), item("Food", self.itt26)
		self.water, self.tip = item("Water", self.itt0), item("TIP", self.itt0)
		self.customer = frappe.db.get_value("Customer", {"disabled": 0}, "name")
		cost_center = frappe.get_cached_value("Company", company, "cost_center") or frappe.db.get_value(
			"Cost Center", {"company": company, "is_group": 0}, "name"
		)
		write_off = frappe.get_cached_value("Company", company, "write_off_account") or frappe.db.get_value(
			"Account", {"company": company, "is_group": 0, "root_type": "Expense"}, "name"
		)

		def till(name, taxes):
			return self.insert(
				"POS Profile",
				__newname=name,
				name=name,
				company=company,
				currency=frappe.get_cached_value("Company", company, "default_currency"),
				warehouse=warehouse,
				selling_price_list=price_list,
				customer=self.customer,
				cost_center=cost_center,
				write_off_account=write_off,
				write_off_cost_center=cost_center,
				write_off_limit=1,
				disable_rounded_total=0,
				taxes_and_charges=taxes,
				payments=[{"mode_of_payment": mode, "default": 1}],
				applicable_for_users=[{"user": "Administrator"}],
			)

		self.till, self.till_excl, self.other_till = till(f"{T} Till", incl), till(f"{T} Till excl", excl), till(f"{T} Other Till", incl)
		self.restaurant = self.insert("Restaurant", restaurant_name=f"{T} Restaurant", company=company, enable_tips=0, tip_item=self.tip)
		self.other_restaurant = self.insert("Restaurant", restaurant_name=f"{T} Other Restaurant", company=company, enable_tips=0)
		# Each till's POS Settings: its restaurant, and prices with the VAT in them or not (POS Next applies it to a sale)
		for profile, restaurant, inclusive in (
			(self.till, self.restaurant, 1),
			(self.till_excl, self.restaurant, 0),
			(self.other_till, self.other_restaurant, 1),
		):
			self.insert(
				"POS Settings",
				pos_profile=profile,
				enabled=1,
				enable_restaurant_mode=1,
				restaurant=restaurant,
				tax_inclusive=inclusive,
			)
		room = self.insert("Restaurant Area", restaurant=self.restaurant, area_name=f"{T} Room")
		self.table = self.insert("Restaurant Table", area=room, table_name=f"{T} T5")
		other_room = self.insert("Restaurant Area", restaurant=self.other_restaurant, area_name=f"{T} Room")
		self.other_table = self.insert("Restaurant Table", area=other_room, table_name=f"{T} T5")
		# The printers: this till's, the other till's, and a default printer the ticket must never take
		self.printer = self.add_printer(f"{T} till printer", self.till, open_drawer="Always", online=1)
		self.add_printer(f"{T} excl printer", self.till_excl, online=1)
		self.other_printer = self.add_printer(f"{T} other printer", self.other_till, online=1)
		self.default = self.add_printer(f"{T} default printer", None, online=1)
		frappe.db.set_single_value(SETTINGS, "default_printer", self.default)
		self.kitchen_sends = []
		kitchen = patch("pos_next.api.kitchen_events.kitchen_send", side_effect=lambda *a, **k: self.kitchen_sends.append(a))
		kitchen.start()
		self.addCleanup(kitchen.stop)
		logo = patch("cloudprnt.receipt_markup.company_logo_path", return_value=None)
		logo.start()
		self.addCleanup(logo.stop)

	def insert(self, doctype, **values):
		name = values.pop("__newname", None)
		doc = frappe.get_doc({"doctype": doctype, **values})
		if name:
			doc.name = name
			doc.flags.name_set = True
		return doc.insert(ignore_permissions=True).name

	def add_printer(self, label, profile, **values):
		row = frappe.get_doc(
			{
				"doctype": PRINTER,
				"parent": SETTINGS,
				"parenttype": SETTINGS,
				"parentfield": "printers",
				"name": frappe.generate_hash(length=10),
				"idx": 900,
				"label": label,
				"pos_profile": profile,
				"paper_width": 80,
				**values,
			}
		)
		row.db_insert()
		return row.name

	def line(self, item, qty, price, discount_percentage=0, **values):
		"""A line as the till's payment sends it (useInvoice.formatItemsForSubmission, prices with the VAT in them): its
		rate after its discount, rounded per unit, and the discount of the whole line beside it."""
		base = round(qty * price, 2)
		discount = round(base * discount_percentage / 100, 2)
		return {
			"item_code": item,
			"item_name": item,
			"qty": qty,
			"rate": round(price - discount / qty, 2),
			"price_list_rate": price,
			"uom": "Nos",
			"conversion_factor": 1,
			"discount_percentage": discount_percentage,
			"discount_amount": discount,
			"is_free_item": 0,
			"posa_item_modifiers": "",
			"posa_special_instructions": "",
			"kds_status": "Pending",
			**values,
		}

	def bad_line(self, field, value, qty=1):
		"""A line as the till sends it, with `field` holding `value` (whatever it is)."""
		line = self.line(self.steak, qty, 38.5)
		line[field] = value
		return line

	def order(self, items, **values):
		return {
			"pos_profile": self.till,
			"customer": self.customer,
			"restaurant_table": self.table,
			"is_takeaway": 0,
			"takeaway_number": "",
			"draft": None,
			"discount_amount": 0,
			"items": items,
			**values,
		}

	def priced(self, order):
		"""The order as the ticket prices it (the same function the print calls)."""
		return provisional_ticket.build_unsaved_invoice(order, frappe.get_cached_doc("POS Profile", order["pos_profile"]))

	def printed(self, order):
		"""Print `order` and give back the job queued for it (its markup, printer, drawer and expiry)."""
		answer = print_provisional_ticket(json.dumps(order))
		job = frappe.get_all(
			jobs.DOCTYPE,
			filters={"job_token": answer["job_token"]},
			fields=["printer", "status", "open_drawer", "expires_at", "creation", "job_data", "reference_name"],
		)[0]
		return answer, job

	def payments_draft(self, order):
		"""What the payment's own draft holds for the same order: pos_next's update_invoice, the step the payment takes
		before it submits (rolled back with the test)."""
		from pos_next.api.invoices import update_invoice

		payload = {
			"doctype": "Sales Invoice",
			"pos_profile": order["pos_profile"],
			"customer": order["customer"],
			"restaurant_table": order["restaurant_table"],
			"discount_amount": order["discount_amount"],
			"items": order["items"],
			"is_pos": 1,
			"update_stock": 1,
		}
		return frappe.get_doc("Sales Invoice", update_invoice(json.dumps(payload))["name"])


	def open_order(self, paid=0, tip=0, table=None, takeaway=None, desk=False, pos_profile=None):
		"""The table's open order, as the till saved it at « Validate », with what a guest paid from their phone. A takeaway
		order (`takeaway`, its number) or a desk order (`desk`) names no table; `pos_profile` is the till that took it."""
		mode = frappe.db.get_value("Mode of Payment Account", {"company": self.company}, "parent")
		items = [{"item_code": self.steak, "qty": 1, "rate": 38.5}]
		if tip:
			items.append({"item_code": self.tip, "qty": 1, "rate": tip})
		doc = frappe.get_doc(
			{
				"doctype": "Sales Invoice",
				"pos_profile": pos_profile or self.till,
				"company": self.company,
				"customer": self.customer,
				"is_pos": 1,
				"items": items,
			}
		)
		doc.set_missing_values()
		# set_missing_values() resets a POS sale's payment rows from its POS Profile: the guest's payment comes after
		doc.set("payments", [])
		if paid:
			doc.append("payments", {"mode_of_payment": mode, "amount": paid})
		doc.insert(ignore_permissions=True)
		self.assertEqual(flt(doc.paid_amount), paid)
		if takeaway or desk:
			values = {"restaurant_table": None, "is_takeaway": 1 if takeaway else 0, "takeaway_number": takeaway or ""}
		else:
			values = {"restaurant_table": table or self.table}
		frappe.db.set_value("Sales Invoice", doc.name, values, update_modified=False)
		return doc.name

	def cashier(self, email, create=True):
		"""A desk account holding one role of the test's own: it reads Sales Invoices and Restaurants, and makes a sale when
		`create`. Nothing else, so that only the right under test can refuse it."""
		role = f"{T} Role {'create' if create else 'read'}"
		if not frappe.db.exists("Role", role):
			self.insert("Role", role_name=role, desk_access=1)
		# ERPNext reads, as the user, what it prices the order from (the item, the receivable account...)
		reads = ("Restaurant", "Account", "Item", "Customer", "POS Profile", "Company", "Price List", "Warehouse", "UOM", "Item Group")
		rules = {"Sales Invoice": {"read": 1, "create": int(create)}, **{doctype: {"read": 1} for doctype in reads}}
		for doctype, rights in rules.items():
			self.insert(
				"Custom DocPerm",
				parent=doctype,
				parenttype="DocType",
				parentfield="permissions",
				role=role,
				permlevel=0,
				**rights,
			)
			frappe.clear_cache(doctype=doctype)
		self.permissions_changed = True
		make_user(email, [role], "System User")
		self.addCleanup(frappe.clear_cache, user=email)
		return email

	@contextmanager
	def at_till(self, pos_profile):
		"""The session user has a shift open at `pos_profile` (POS Next reads the restaurant a cashier works for there)."""
		with patch("pos_next.restaurant.settings.open_shift_profile", return_value=pos_profile):
			yield


class TestTheAmountsAreERPNexts(RealTill):
	def test_a_discounted_line_is_printed_at_what_the_payment_charges_never_at_its_full_price(self):
		# C1: 3 x 12.35 at 10 %. The till's payment sends 11.12 a unit (its rate after the discount, rounded per unit) and
		# ERPNext charges 33.36; the cart takes 3.69 off the line to come to it (01fab002; it took 3.70 off and showed 33.35
		# before). The ticket prints what the payment will charge, never the price before the discount (37.05).
		# what the till's payment sends for it (provisionalTicket.spec.js asserts these numbers from the real cart)
		line = self.line(self.steak, 3, 12.35, 10, discount_amount=3.69, rate=11.12)
		order = self.order([line])
		doc = self.priced(order)
		self.assertEqual(flt(doc.items[0].amount), 33.36)
		self.assertNotEqual(flt(doc.items[0].amount), 37.05, "never the price before the discount")
		paid = self.payments_draft(order)
		self.assertEqual(flt(paid.items[0].amount), flt(doc.items[0].amount), "the payment's own draft charges the same")
		self.assertEqual(flt(paid.grand_total), flt(doc.grand_total))
		self.assertEqual(flt(paid.rounded_total), flt(doc.rounded_total))
		self.assertEqual(
			[flt(tax.tax_amount_after_discount_amount) for tax in paid.taxes],
			[flt(tax.tax_amount_after_discount_amount) for tax in doc.taxes],
		)
		_answer, job = self.printed(order)
		self.assertIn(f"right {chf(33.36)}]", job.job_data)
		self.assertNotIn(chf(37.05), job.job_data)

	def test_the_total_is_rounded_to_the_5_centimes_and_the_rounding_is_said(self):
		order = self.order([self.line(self.steak, 3, 12.35, 10, discount_amount=3.69, rate=11.12)])
		doc = self.priced(order)
		self.assertEqual((flt(doc.grand_total), flt(doc.rounded_total)), (33.36, 33.35))
		_answer, job = self.printed(order)
		self.assertIn(f"[column: left {frappe._('Rounding Adjustment')}:; right {chf(-0.01)}]", job.job_data)
		self.assertIn(f"right {chf(33.35)}]", job.job_data)

	def test_after_an_order_discount_the_vat_printed_is_the_vat_owed(self):
		# I1: 2 x 38.50 + 8.00 = 85.00 with the VAT in the prices, 5.00 off the order: the VAT owed is on 80.00
		order = self.order([self.line(self.steak, 2, 38.5), self.line(self.wine, 1, 8)], discount_amount=5)
		doc = self.priced(order)
		owed = round(80 * 8.1 / 108.1, 2)
		self.assertEqual(flt(doc.taxes[0].tax_amount_after_discount_amount), owed)
		self.assertNotEqual(flt(doc.taxes[0].tax_amount), owed, "ERPNext keeps the VAT before the discount there")
		self.assertEqual(flt(doc.grand_total), 80)
		_answer, job = self.printed(order)
		self.assertIn(f"[column: left {vat('8.1')}:; right {chf(owed)}]", job.job_data)
		self.assertNotIn(chf(doc.taxes[0].tax_amount), job.job_data)
		self.assertIn(f"[column: left {frappe._('Discount')}:; right {chf(5)}]", job.job_data)
		# the paid receipt prints the same VAT, from the same helper
		receipt = get_receipt_markup(doc)
		self.assertIn(f"[column: left {vat('8.1')}:; right {chf(owed)}]", receipt)
		self.assertIn(f"{vat('8.1')}: {chf(80 - owed)} + {chf(owed)} = {chf(80)}", receipt)

	def test_a_whole_cart_coupon_is_on_the_ticket_as_the_payment_applies_it(self):
		# I5: the payment sends a whole-cart coupon as the order's discount (effectiveHeaderDiscount)
		order = self.order([self.line(self.steak, 2, 38.5)], discount_amount=10, coupon_code="_TEST-PROV-COUPON")
		_answer, job = self.printed(order)
		self.assertIn(f"[column: left {frappe._('Discount')}:; right {chf(10)}]", job.job_data)
		self.assertIn(f"right {chf(67)}]", job.job_data)

	def test_a_price_set_by_hand_is_the_one_printed_when_the_till_lets_the_cashier_set_it(self):
		frappe.db.set_value("POS Settings", {"pos_profile": self.till}, "allow_user_to_edit_rate", 1)
		line = self.line(self.steak, 2, 38.5, is_rate_manually_edited=1)
		line["rate"] = 30
		doc = self.priced(self.order([line]))
		self.assertEqual(flt(doc.items[0].amount), 60)

	def test_two_vat_rates_print_two_lines_that_add_up_with_the_net_to_the_total(self):
		# a takeaway dish at 2.6 % and a bottle at 8.1 % in one order, prices with the VAT in them
		order = self.order([self.line(self.food, 2, 12.5), self.line(self.wine, 1, 8)])
		doc = self.priced(order)
		by_account = {tax.account_head: flt(tax.tax_amount_after_discount_amount) for tax in doc.taxes}
		self.assertEqual(by_account[self.vat26], round(25 * 2.6 / 102.6, 2))
		self.assertEqual(by_account[self.vat81], round(8 * 8.1 / 108.1, 2))
		_answer, job = self.printed(order)
		self.assertIn(f"[column: left {vat('8.1')}:; right {chf(by_account[self.vat81])}]", job.job_data)
		self.assertIn(f"[column: left {vat('2.6')}:; right {chf(by_account[self.vat26])}]", job.job_data)
		self.assertNotIn("Due", job.job_data, "the label is the rate, not the account's name")
		self.assertEqual(round(flt(doc.net_total) + sum(by_account.values()), 2), flt(doc.grand_total))
		receipt = get_receipt_markup(doc)
		self.assertIn(f"[column: left {vat('2.6')}:; right {chf(by_account[self.vat26])}]", receipt)
		self.assertNotIn("Due", receipt)

	def test_prices_before_tax_show_the_net_total_and_the_vat_on_top(self):
		order = self.order([self.line(self.food, 2, 12.5), self.line(self.wine, 1, 8)], pos_profile=self.till_excl)
		doc = self.priced(order)
		self.assertEqual(flt(doc.net_total), 33)
		vat26, vat81 = round(25 * 0.026, 2), round(8 * 0.081, 2)
		self.assertEqual(flt(doc.grand_total), round(33 + vat26 + vat81, 2))
		_answer, job = self.printed(order)
		self.assertIn(f"[column: left {frappe._('Net Total')}:; right {chf(33)}]", job.job_data)
		self.assertIn(f"[column: left {vat('2.6')}:; right {chf(vat26)}]", job.job_data)
		self.assertIn(f"[column: left {vat('8.1')}:; right {chf(vat81)}]", job.job_data)

	def test_the_vat_is_included_or_on_top_as_the_tills_settings_say_as_on_the_payment(self):
		# The till's template says the VAT is in the prices, its POS Settings say it is not: POS Next follows the settings
		# on the payment's draft, and the ticket follows them too
		frappe.db.set_value("POS Settings", {"pos_profile": self.till}, "tax_inclusive", 0)
		order = self.order([self.line(self.food, 2, 12.5), self.line(self.wine, 1, 8)])
		doc = self.priced(order)
		paid = self.payments_draft(order)
		self.assertEqual(flt(doc.net_total), 33)
		self.assertEqual(flt(doc.grand_total), flt(paid.grand_total))
		self.assertEqual(flt(doc.grand_total), round(33 + round(25 * 0.026, 2) + round(8 * 0.081, 2), 2))

	def test_an_order_without_vat_has_no_vat_line(self):
		_answer, job = self.printed(self.order([self.line(self.water, 2, 3)]))
		self.assertNotIn(frappe._("VAT {0} %").split("{0}")[0], job.job_data)
		self.assertIn(f"right {chf(6)}]", job.job_data)

	def test_a_line_with_options_prints_them_under_it(self):
		modifiers = '[{"group": "Cooking", "options": [{"name": "Medium", "price_adjustment": 0}]}]'
		_answer, job = self.printed(self.order([self.line(self.steak, 1, 38.5, posa_item_modifiers=modifiers, posa_special_instructions="No salt")]))
		self.assertIn("   Cooking: Medium", job.job_data)
		self.assertIn("   No salt", job.job_data)
		self.assertNotIn("price_adjustment", job.job_data)

	def test_an_offered_row_charges_nothing_and_says_offered(self):
		# Task 24: the offered part is a row of its own at its list price with a 100 % discount
		offered = self.line(self.wine, 1, 9.5, 100, posa_is_offered=1)
		order = self.order([self.line(self.steak, 1, 38.5), offered])
		doc = self.priced(order)
		self.assertEqual(flt(doc.items[1].amount), 0)
		_answer, job = self.printed(order)
		self.assertIn(f"   {frappe._('Offered', context='Given for free')}", job.job_data)
		self.assertIn(f"right {chf(38.5)}]", job.job_data)

	def test_what_a_guest_paid_by_phone_is_read_from_the_tables_order_and_left_to_pay_is_said(self):
		draft = self.open_order(paid=22, tip=2)
		_answer, job = self.printed(self.order([self.line(self.steak, 2, 38.5)], draft=draft))
		self.assertIn(f"[column: left {frappe._('Already paid')}:; right {chf(20)}]", job.job_data)
		self.assertIn(f"[column: left {frappe._('Left to pay')}:; right {chf(57)}]", job.job_data)
		self.assertEqual(job.reference_name, draft)

class TestPrintingWritesNothingButTheJob(RealTill):
	def test_nothing_is_saved_no_kitchen_no_drawer_only_the_print_job(self):
		draft = self.open_order(paid=10)
		modified = frappe.db.get_value("Sales Invoice", draft, "modified")
		counts = {doctype: frappe.db.count(doctype) for doctype in ("Sales Invoice", "Sales Invoice Item", "Comment", "Version", drawer.DOCTYPE, "Error Log")}
		written = []
		real_sql = frappe.db.sql

		def watch(query, *args, **kwargs):
			text = str(query).strip().lower()
			if text.startswith(("insert", "update", "delete", "replace")):
				written.append(text[:120])
			return real_sql(query, *args, **kwargs)

		order = self.order([self.line(self.steak, 2, 38.5), self.line(self.wine, 1, 8)], draft=draft)
		with patch.object(frappe.db, "sql", side_effect=watch):
			answer = print_provisional_ticket(json.dumps(order))
		self.assertTrue(written, "the job is written")
		self.assertTrue(all("`tabcloudprnt print queue`" in query for query in written), written)
		self.assertEqual(counts, {doctype: frappe.db.count(doctype) for doctype in counts})
		self.assertEqual(frappe.db.get_value("Sales Invoice", draft, "modified"), modified, "the table's order is not touched")
		self.assertEqual(self.kitchen_sends, [], "nothing reaches the kitchen")
		job = frappe.db.get_value(jobs.DOCTYPE, {"job_token": answer["job_token"]}, ["open_drawer", "expires_at", "creation", "printer"], as_dict=True)
		self.assertEqual(job.open_drawer or "", "", "a provisional ticket never opens the drawer")
		self.assertIsNone(jobs.drawer_for(job))
		self.assertLessEqual((job.expires_at - job.creation).total_seconds(), jobs.RECEIPT_VALID_MINUTES * 60 + 1)
		self.assertGreater((job.expires_at - job.creation).total_seconds(), 60)

	def test_the_printer_is_the_tills_never_one_the_caller_names_nor_the_default(self):
		order = self.order([self.line(self.steak, 1, 38.5)])
		for printer in (" ", "", f"{T} other printer", self.default, {"name": self.other_printer}):
			with self.subTest(printer=printer):
				answer = frappe.call(print_provisional_ticket, order=json.dumps(order), printer=printer)
				printed_on = frappe.db.get_value(jobs.DOCTYPE, {"job_token": answer["job_token"]}, "printer")
				self.assertEqual(printed_on, self.printer)

	def test_a_blank_or_a_dict_till_is_refused_cleanly(self):
		for till in (" ", "", None, {"name": self.till}, ["x"]):
			with self.subTest(till=till), self.assertRaises(frappe.ValidationError):
				print_provisional_ticket(json.dumps(self.order([self.line(self.steak, 1, 38.5)], pos_profile=till)))

	def test_a_till_without_a_printer_is_said_so_and_nothing_is_queued(self):
		frappe.db.delete(PRINTER, {"pos_profile": self.till})
		before = frappe.db.count(jobs.DOCTYPE)
		with self.assertRaises(NoTicketPrinter):
			print_provisional_ticket(json.dumps(self.order([self.line(self.steak, 1, 38.5)])))
		self.assertEqual(frappe.db.count(jobs.DOCTYPE), before)

	def test_the_answer_says_when_the_printer_is_offline(self):
		frappe.db.set_value(PRINTER, self.printer, "online", 0)
		answer = print_provisional_ticket(json.dumps(self.order([self.line(self.steak, 1, 38.5)])))
		self.assertFalse(answer["printer_online"])
		self.assertEqual(answer["valid_minutes"], jobs.RECEIPT_VALID_MINUTES)

	def test_the_tickets_status_follows_its_job(self):
		answer = print_provisional_ticket(json.dumps(self.order([self.line(self.steak, 1, 38.5)])))
		self.assertEqual(ticket_status(answer["job_token"])["status"], "Pending")
		frappe.db.set_value(jobs.DOCTYPE, {"job_token": answer["job_token"]}, {"status": "Failed", "result_code": "Paper out"})
		self.assertEqual(ticket_status(answer["job_token"]), {"status": "Failed", "printer_online": True, "reason": "Paper out"})
		for job in (None, "", {"job_token": answer["job_token"]}, "no-such-job"):
			with self.subTest(job=job), self.assertRaises(frappe.DoesNotExistError):
				ticket_status(job)


class TestWhatIsRefused(RealTill):
	def test_a_portal_account_is_refused_whatever_its_roles(self):
		make_user(PORTAL, ["Customer"], "Website User")
		make_user(PORTAL_WITH_ROLES, ["Accounts User", "POSNext Cashier"], "Website User")
		order = json.dumps(self.order([self.line(self.steak, 1, 38.5)]))
		for user in (PORTAL, PORTAL_WITH_ROLES):
			with self.subTest(user=user), as_user(user):
				with self.assertRaises(frappe.PermissionError):
					print_provisional_ticket(order)
				with self.assertRaises(frappe.PermissionError):
					ticket_status("no-such-job")

	def test_a_desk_user_who_may_not_make_sales_is_refused(self):
		make_user(DESK_WITHOUT_INVOICES, ["Blogger"], "System User")
		with as_user(DESK_WITHOUT_INVOICES), self.assertRaises(frappe.PermissionError):
			print_provisional_ticket(json.dumps(self.order([self.line(self.steak, 1, 38.5)])))

	def test_a_till_does_not_print_another_restaurants_table(self):
		with self.assertRaises(frappe.PermissionError):
			print_provisional_ticket(json.dumps(self.order([self.line(self.steak, 1, 38.5)], restaurant_table=self.other_table)))

	def test_a_cashier_kept_to_another_restaurant_is_refused(self):
		# he works at this till (a shift open here) and may make sales: only his User Permission can refuse him
		self.cashier(CASHIER)
		self.insert("User Permission", user=CASHIER, allow="Restaurant", for_value=self.other_restaurant)
		frappe.clear_cache(user=CASHIER)
		order = json.dumps(self.order([self.line(self.steak, 1, 38.5)], is_takeaway=1, restaurant_table=None))
		with as_user(CASHIER), self.at_till(self.till), self.assertRaises(frappe.PermissionError):
			print_provisional_ticket(order)

	def test_a_paid_or_cancelled_order_is_refused(self):
		draft = self.open_order()
		frappe.db.set_value("Sales Invoice", draft, "docstatus", 1, update_modified=False)
		with self.assertRaises(frappe.ValidationError) as refused:
			print_provisional_ticket(json.dumps(self.order([self.line(self.steak, 1, 38.5)], draft=draft)))
		self.assertIn(draft, str(refused.exception))
		frappe.db.set_value("Sales Invoice", draft, "docstatus", 2, update_modified=False)
		with self.assertRaises(frappe.ValidationError):
			print_provisional_ticket(json.dumps(self.order([self.line(self.steak, 1, 38.5)], draft=draft)))

	def test_another_tables_order_is_refused(self):
		draft = self.open_order(table=self.other_table)
		with self.assertRaises(frappe.ValidationError):
			print_provisional_ticket(json.dumps(self.order([self.line(self.steak, 1, 38.5)], draft=draft)))

	def test_an_order_with_no_lines_or_no_table_is_refused(self):
		with self.assertRaises(frappe.ValidationError) as refused:
			print_provisional_ticket(json.dumps(self.order([])))
		self.assertIn(frappe._("there is nothing to print"), str(refused.exception))
		with self.assertRaises(frappe.ValidationError):
			print_provisional_ticket(json.dumps(self.order([self.line(self.steak, 1, 38.5)], restaurant_table=None)))
		with self.assertRaises(frappe.ValidationError):
			print_provisional_ticket("not json")

	def test_the_endpoints_are_not_open_to_kitchen_tablets(self):
		"""Task 16's device accounts call only the methods it lists (M10): the ticket is not one of them."""
		try:
			from pos_next.restaurant import devices
		except ImportError:
			raise unittest.SkipTest("Task 16's kitchen devices are not in this POS Next") from None
		methods = set(getattr(devices, "TABLET_METHODS", ()))
		self.assertFalse({m for m in methods if "provisional_ticket" in m}, methods)


class TestWhoMayPrint(RealTill):
	"""The right to make a sale, and the restaurant a cashier works for."""

	def order_json(self, **values):
		return json.dumps(self.order([self.line(self.steak, 1, 38.5)], **values))

	def test_a_user_who_reads_sales_invoices_and_restaurants_but_may_not_create_one_is_refused(self):
		answer = print_provisional_ticket(self.order_json())
		self.cashier(READER, create=False)
		with as_user(READER), self.at_till(self.till):
			self.assertTrue(frappe.has_permission("Sales Invoice", "read"))
			self.assertTrue(frappe.has_permission("Restaurant", "read"))
			self.assertFalse(frappe.has_permission("Sales Invoice", "create"))
			with self.assertRaises(frappe.PermissionError):
				print_provisional_ticket(self.order_json())
			with self.assertRaises(frappe.PermissionError):
				ticket_status(answer["job_token"])
		# the same rights plus the one to make a sale: he prints and follows his ticket
		self.cashier(CASHIER, create=True)
		with as_user(CASHIER), self.at_till(self.till):
			own = print_provisional_ticket(self.order_json())
			self.assertEqual(ticket_status(own["job_token"])["status"], "Pending")

	def test_a_cashier_of_another_restaurant_prints_on_no_till_of_this_one(self):
		self.cashier(CASHIER)
		takeaway = self.order_json(restaurant_table=None, is_takeaway=1, takeaway_number="T-1")
		before = frappe.db.count(jobs.DOCTYPE)
		with as_user(CASHIER), self.at_till(self.other_till):
			for order in (self.order_json(), takeaway):
				with self.assertRaises(frappe.PermissionError):
					print_provisional_ticket(order)
		self.assertEqual(frappe.db.count(jobs.DOCTYPE), before, "nothing is queued on this restaurant's printer")
		# at a till of this restaurant, the same cashier prints on it
		with as_user(CASHIER), self.at_till(self.till):
			for order in (self.order_json(), takeaway):
				answer = print_provisional_ticket(order)
				self.assertEqual(frappe.db.get_value(jobs.DOCTYPE, {"job_token": answer["job_token"]}, "printer"), self.printer)
		# and at the other restaurant's till he prints that restaurant's tickets
		other = json.dumps(self.order([self.line(self.steak, 1, 38.5)], pos_profile=self.other_till, restaurant_table=self.other_table))
		with as_user(CASHIER), self.at_till(self.other_till):
			answer = print_provisional_ticket(other)
			self.assertEqual(frappe.db.get_value(jobs.DOCTYPE, {"job_token": answer["job_token"]}, "printer"), self.other_printer)

	def test_a_cashier_of_another_restaurant_does_not_read_the_status_of_this_ones_tickets(self):
		answer = print_provisional_ticket(self.order_json())
		self.cashier(CASHIER)
		with as_user(CASHIER):
			with self.at_till(self.other_till), self.assertRaises(frappe.PermissionError):
				ticket_status(answer["job_token"])
			with self.at_till(self.till):
				self.assertEqual(ticket_status(answer["job_token"])["status"], "Pending")

	def test_a_till_manager_prints_on_any_restaurants_till_without_a_shift_open(self):
		self.cashier(MANAGER)
		role = "Nexus POS Manager" if frappe.db.exists("Role", "Nexus POS Manager") else "Admin"
		if not frappe.db.exists("Role", role):
			raise unittest.SkipTest("The site has no till manager role")
		frappe.get_doc("User", MANAGER).add_roles(role)
		frappe.clear_cache(user=MANAGER)
		with as_user(MANAGER), self.at_till(None):
			for till, table in ((self.till, self.table), (self.other_till, self.other_table)):
				order = json.dumps(self.order([self.line(self.steak, 1, 38.5)], pos_profile=till, restaurant_table=table))
				self.assertTrue(print_provisional_ticket(order)["success"])


class TestTheOrderANamedDraftMayBe(RealTill):
	def takeaway(self, **values):
		return self.order([self.line(self.steak, 1, 38.5)], restaurant_table=None, is_takeaway=1, takeaway_number="T-1", **values)

	def test_a_takeaway_order_may_not_name_any_other_open_order(self):
		cases = {
			"a table's order": self.open_order(paid=33),
			"a desk order": self.open_order(desk=True),
			"a takeaway order of another restaurant's till": self.open_order(takeaway="T-2", pos_profile=self.other_till),
			"a takeaway order of another till of this restaurant": self.open_order(takeaway="T-3", pos_profile=self.till_excl),
		}
		before = frappe.db.count(jobs.DOCTYPE)
		for label, draft in cases.items():
			with self.subTest(label), self.assertRaises(frappe.ValidationError) as refused:
				print_provisional_ticket(json.dumps(self.takeaway(draft=draft)))
			self.assertIn(draft, str(refused.exception))
		self.assertEqual(frappe.db.count(jobs.DOCTYPE), before)

	def test_a_takeaway_order_names_its_own_open_order_and_gets_what_a_guest_paid(self):
		draft = self.open_order(takeaway="T-1", paid=10)
		_answer, job = self.printed(self.takeaway(draft=draft))
		self.assertIn(f"[column: left {frappe._('Already paid')}:; right {chf(10)}]", job.job_data)
		self.assertEqual(job.reference_name, draft)


# //// Neoffice — the notes of a table (POS Next, NeoResto lot 1, notes): a table may hold several open orders, one per party, told ".1", ".2"
class TestTheNotesOfATable(RealTill):
	"""The ticket is of the note the till names: it reads the order of THAT note, not any order of the table, and its title tells the
	rank (and the party's name) once the table holds a second open order."""

	def note_draft(self, rank, party=None, table=None):
		"""An open order of the table at this rank, made the plain way (no POS payment rows): the rank and the party are what these tests look at."""
		doc = frappe.get_doc(
			{
				"doctype": "Sales Invoice",
				"company": self.company,
				"customer": self.customer,
				"items": [{"item_code": self.steak, "qty": 1, "rate": 38.5}],
			}
		)
		doc.insert(ignore_permissions=True)
		frappe.db.set_value(
			"Sales Invoice",
			doc.name,
			{"restaurant_table": table or self.table, "restaurant_note": rank, "restaurant_note_name": party or ""},
			update_modified=False,
		)
		return doc.name

	def notes(self):
		"""The table's two open orders: its first note, and its second, named."""
		return self.note_draft(1), self.note_draft(2, "Dupont")

	def lines(self):
		return [self.line(self.steak, 2, 38.5)]

	def test_the_ticket_of_a_note_reads_the_order_of_that_note(self):
		first, second = self.notes()
		for note, draft in ((1, first), (2, second)):
			with self.subTest(note=note):
				_answer, job = self.printed(self.order(self.lines(), restaurant_note=note))
				self.assertEqual(job.reference_name, draft)

	def test_the_rank_may_be_written_as_text(self):
		_first, second = self.notes()
		_answer, job = self.printed(self.order(self.lines(), restaurant_note="2"))
		self.assertEqual(job.reference_name, second)

	def test_a_note_that_has_no_order_yet_reads_no_order(self):
		self.notes()
		_answer, job = self.printed(self.order(self.lines(), restaurant_note=3))
		self.assertFalse(job.reference_name)

	def test_a_till_that_knows_no_notes_reads_the_most_recently_changed_order_as_it_always_did(self):
		_first, second = self.notes()
		_answer, job = self.printed(self.order(self.lines()))
		self.assertEqual(job.reference_name, second)

	def test_a_rank_nobody_can_open_reads_as_none_sent(self):
		_first, second = self.notes()
		for sent in (0, -1, 100, "abc", 2.5, True, [1], {"note": 1}):
			with self.subTest(sent=sent):
				_answer, job = self.printed(self.order(self.lines(), restaurant_note=sent))
				self.assertEqual(job.reference_name, second)

	def test_the_title_names_the_rank_and_the_party_when_the_table_holds_several_notes(self):
		self.notes()
		_answer, job = self.printed(self.order(self.lines(), restaurant_note=2))
		self.assertIn(f"{T} T5.1 \u00b7 Dupont[magnify]", job.job_data)
		_answer, job = self.printed(self.order(self.lines(), restaurant_note=1))
		self.assertIn(f"{T} T5.0[magnify]", job.job_data)

	def test_a_note_the_till_has_not_saved_yet_is_one_more_note_in_the_title(self):
		self.note_draft(1)
		_answer, job = self.printed(self.order(self.lines(), restaurant_note=2, restaurant_note_name="Martin"))
		self.assertIn(f"{T} T5.1 \u00b7 Martin[magnify]", job.job_data)

	def test_the_title_of_a_table_with_one_order_is_its_label_alone(self):
		draft = self.note_draft(1)
		for order in (self.order(self.lines()), self.order(self.lines(), restaurant_note=1, draft=draft)):
			with self.subTest(order=order):
				_answer, job = self.printed(order)
				self.assertIn(f"{T} T5[magnify]", job.job_data)
				self.assertNotIn(f"{T} T5.", job.job_data)

	def test_the_party_of_the_saved_order_is_told_when_the_till_names_only_the_draft(self):
		_first, second = self.notes()
		_answer, job = self.printed(self.order(self.lines(), draft=second))
		self.assertIn(f"{T} T5.1 \u00b7 Dupont[magnify]", job.job_data)

	def test_a_kitchen_ticket_names_the_note_of_its_order_too(self):
		from cloudprnt.kitchen_ticket import _where
		from cloudprnt.labels import table_label_of_order

		first, second = self.notes()
		for draft, label in ((first, f"{T} T5.0"), (second, f"{T} T5.1 \u00b7 Dupont")):
			with self.subTest(draft=draft):
				doc = frappe.get_doc("Sales Invoice", draft)
				self.assertEqual(table_label_of_order(doc), label)
				self.assertEqual(_where(doc), label)

	def test_an_order_of_no_table_has_no_note_to_name(self):
		from cloudprnt.labels import table_label_of_order

		self.notes()
		self.assertEqual(table_label_of_order(Order(restaurant_table=None, name=None)), "")


class TestLimits(RealTill):
	def refused(self, order, kind=frappe.ValidationError):
		before = frappe.db.count(jobs.DOCTYPE)
		with self.assertRaises(kind) as refused:
			print_provisional_ticket(json.dumps(order))
		self.assertEqual(frappe.db.count(jobs.DOCTYPE), before, "nothing is queued")
		return refused.exception

	def test_a_remark_or_an_option_is_cut_at_the_limit_and_says_so(self):
		self.assertEqual(provisional_ticket.MAX_REMARK_CHARS, 500)
		long = "x" * 600
		options = json.dumps([{"group": "Cooking", "options": [{"name": long}]}])
		line = self.line(self.steak, 1, 38.5, posa_special_instructions=long, posa_item_modifiers=options)
		_answer, job = self.printed(self.order([line]))
		self.assertIn("   " + "x" * 497 + "...\n", job.job_data, "the remark")
		self.assertIn("   Cooking: " + "x" * 488 + "...\n", job.job_data, "the option, with its group in the limit")
		self.assertNotIn("x" * 498, job.job_data)

	def test_a_dish_name_is_cut_at_the_length_of_an_item_name(self):
		self.assertEqual(provisional_ticket.MAX_NAME_CHARS, 140)
		_answer, job = self.printed(self.order([self.line(self.steak, 1, 38.5, item_name="n" * 200)]))
		self.assertIn("1 x " + "n" * 137 + "...", job.job_data)
		self.assertNotIn("n" * 138, job.job_data)

	def test_a_field_far_beyond_any_remark_is_refused(self):
		for field in ("posa_special_instructions", "posa_item_modifiers", "item_name"):
			with self.subTest(field=field):
				self.refused(self.order([self.line(self.steak, 1, 38.5, **{field: "x" * 5001})]))

	def test_an_order_holds_at_most_200_lines(self):
		self.assertEqual(provisional_ticket.MAX_LINES, 200)
		self.assertTrue(print_provisional_ticket(json.dumps(self.order([self.line(self.water, 1, 3)] * 200)))["success"])
		refused = self.refused(self.order([self.line(self.water, 1, 3)] * 201))
		self.assertIn("200", str(refused))

	def test_a_number_that_is_not_one_or_not_finite_or_beyond_a_million_is_refused(self):
		bad = [
			("qty", float("nan")),
			("qty", float("inf")),
			("qty", "abc"),
			("qty", 1e7),
			("qty", {"a": 1}),
			("qty", [1]),
			("rate", float("-inf")),
			("rate", 1e6 + 1),
			("rate", "1; DROP"),
			("price_list_rate", float("nan")),
			("conversion_factor", "x"),
			("discount_amount", float("nan")),
			("discount_percentage", 101),
			("discount_percentage", -1),
			("discount_percentage", "abc"),
		]
		for field, value in bad:
			with self.subTest(field=field, value=value):
				self.refused(self.order([self.bad_line(field, value)]))
		for value in ("abc", float("nan"), float("inf"), 1e9, {"a": 1}):
			with self.subTest(order_discount=value):
				self.refused(self.order([self.line(self.steak, 1, 38.5)], discount_amount=value))

	def test_a_discount_beyond_what_it_takes_off_is_refused(self):
		# a line's discount cannot exceed its total (2 x 38.50)
		self.refused(self.order([self.line(self.steak, 2, 38.5, discount_amount=100)]))
		# nor the order's discount the order's total
		self.refused(self.order([self.line(self.steak, 2, 38.5)], discount_amount=1000))

	def test_a_number_written_as_text_is_still_a_number(self):
		line = self.line(self.steak, 2, 38.5)
		line.update(qty="2", rate="38.5", price_list_rate=" 38.5 ")
		_answer, job = self.printed(self.order([line]))
		self.assertIn(f"[column: left 2 x {self.steak}; right {chf(77)}]", job.job_data)

	def test_a_user_prints_a_limited_number_of_tickets_a_minute(self):
		self.assertEqual(provisional_ticket.PRINTS_PER_MINUTE, 20)
		order = json.dumps(self.order([self.line(self.steak, 1, 38.5)]))
		with patch.object(provisional_ticket, "PRINTS_PER_MINUTE", 3):
			tokens = [print_provisional_ticket(order)["job_token"] for _ in range(3)]
			before = frappe.db.count(jobs.DOCTYPE)
			with self.assertRaises(frappe.RateLimitExceededError):
				print_provisional_ticket(order)
			self.assertEqual(frappe.db.count(jobs.DOCTYPE), before)
			# following a ticket is not printing one: the button polls every second and a half
			for _ in range(10):
				self.assertEqual(ticket_status(tokens[0])["status"], "Pending")
			# another user has his own count
			self.cashier(CASHIER)
			with as_user(CASHIER), self.at_till(self.till):
				self.assertTrue(print_provisional_ticket(order)["success"])
			# the count is for a minute
			keys = frappe.cache().get_keys("cloudprnt_provisional_ticket")
			self.assertTrue(keys)
			for key in keys:
				self.assertTrue(0 < frappe.cache().ttl(key) <= 60, "the count expires with its minute")
			frappe.cache().delete_keys("cloudprnt_")
			self.assertTrue(print_provisional_ticket(order)["success"])


class TestAHandSetPrice(RealTill):
	def hand_set(self, rate):
		line = self.line(self.steak, 2, 38.5, is_rate_manually_edited=1)
		line["rate"] = rate
		return self.order([line])

	def test_a_till_that_forbids_rate_edits_refuses_a_price_set_by_hand_as_the_payment_does(self):
		order = self.hand_set(30)
		before = frappe.db.count(jobs.DOCTYPE)
		with self.assertRaises(frappe.ValidationError) as refused:
			print_provisional_ticket(json.dumps(order))
		self.assertIn("Rate editing is not allowed", str(refused.exception))
		self.assertEqual(frappe.db.count(jobs.DOCTYPE), before)
		with self.assertRaises(frappe.ValidationError) as paid:
			self.payments_draft(order)
		self.assertEqual(str(refused.exception), str(paid.exception), "the payment says no with the same words")

	def test_a_till_that_allows_rate_edits_prints_the_price_within_the_maximum_discount(self):
		frappe.db.set_value("POS Settings", {"pos_profile": self.till}, {"allow_user_to_edit_rate": 1, "max_discount_allowed": 10})
		_answer, job = self.printed(self.hand_set(36))
		self.assertIn(f"right {chf(72)}]", job.job_data)
		with self.assertRaises(frappe.ValidationError) as refused:
			print_provisional_ticket(json.dumps(self.hand_set(30)))
		self.assertIn("exceeds the maximum allowed discount", str(refused.exception))


class TestABadPayloadIsARefusalNotACrash(RealTill):
	def test_a_line_whose_code_or_texts_are_not_text_is_refused_cleanly(self):
		bad = [
			("item_code", {"a": "b'c"}),
			("item_code", ["x"]),
			("item_name", {"a": 1}),
			("uom", ["Nos"]),
			("warehouse", {"a": 1}),
			("posa_special_instructions", {"a": 1}),
			("posa_special_instructions", ["x"]),
			("posa_item_modifiers", ["x"]),
		]
		for field, value in bad:
			with self.subTest(field=field), self.assertRaises(frappe.ValidationError):
				print_provisional_ticket(json.dumps(self.order([self.bad_line(field, value)])))

	def test_a_number_given_as_a_remark_or_a_takeaway_number_is_printed_as_text(self):
		_answer, job = self.printed(self.order([self.line(self.steak, 1, 38.5, posa_special_instructions=5)]))
		self.assertIn("\n   5\n", job.job_data)
		order = self.order([self.line(self.steak, 1, 38.5)], restaurant_table=None, is_takeaway=1, takeaway_number=12)
		_answer, job = self.printed(order)
		self.assertIn(f"{frappe._('Takeaway')} 12", job.job_data)
		order["takeaway_number"] = {"a": 1}
		_answer, job = self.printed(order)
		self.assertNotIn("{", job.job_data, "a takeaway number that is not text or a number is left out")

	def test_a_validation_error_of_a_kind_the_till_does_not_know_is_a_plain_refusal(self):
		order = json.dumps(self.order([self.line(self.steak, 1, 38.5)]))
		for kind in (frappe.LinkValidationError, frappe.MandatoryError, frappe.UpdateAfterSubmitError):
			with self.subTest(kind=kind.__name__):
				with patch.object(provisional_ticket, "build_unsaved_invoice", side_effect=kind("Something is broken")):
					with self.assertRaises(frappe.ValidationError) as refused:
						print_provisional_ticket(order)
				self.assertIs(type(refused.exception), frappe.ValidationError, "the till knows ValidationError by its name")
				self.assertIn("Something is broken", str(refused.exception))

	def test_the_refusals_the_till_knows_by_name_keep_their_names(self):
		order = self.order([self.line(self.steak, 1, 38.5)])
		with patch.object(provisional_ticket, "build_unsaved_invoice", side_effect=NoTicketPrinter("no")):
			with self.assertRaises(NoTicketPrinter):
				print_provisional_ticket(json.dumps(order))
		with patch.object(provisional_ticket, "build_unsaved_invoice", side_effect=frappe.DoesNotExistError("gone")):
			with self.assertRaises(frappe.DoesNotExistError):
				print_provisional_ticket(json.dumps(order))
		order["items"][0]["item_code"] = "NO SUCH ITEM"
		with self.assertRaises(frappe.DoesNotExistError):
			print_provisional_ticket(json.dumps(order))


class TestWhatTheJobHolds(RealTill):
	def test_no_control_character_reaches_the_printers_job(self):
		line = self.line(
			self.steak,
			1,
			38.5,
			item_name=HOSTILE,
			posa_special_instructions=HOSTILE,
			posa_item_modifiers=json.dumps([{"group": HOSTILE, "options": [{"name": HOSTILE}]}]),
		)
		order = self.order([line], restaurant_table=None, is_takeaway=1, takeaway_number=HOSTILE)
		_answer, job = self.printed(order)
		self.assertEqual(unsafe_characters(job.job_data), [])
		self.assertIn(f"{frappe._('Takeaway')} {CLEAN}", job.job_data)
		self.assertIn(f"   {CLEAN}: {CLEAN}", job.job_data)
