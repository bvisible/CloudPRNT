"""The provisional ticket of a table order: what the table ordered and what it comes to, printed before it pays.

It is built on the server from the order's draft Sales Invoice, so the VAT of each rate and the rounded total are the
invoice's own; it says it is not a receipt, shows no payment, never opens the cash drawer, and expires like a receipt.
"""

import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import fmt_money, formatdate, nowdate

from cloudprnt import drawer, provisional_ticket
from cloudprnt import print_queue_manager as jobs
from cloudprnt.printers import PRINTER, SETTINGS
from cloudprnt.provisional_ticket import get_provisional_ticket_markup, print_provisional_ticket

PROFILE = "_Test CloudPRNT Provisional Till"
OTHER_PROFILE = "_Test CloudPRNT Other Till"
# POS Next names a new table by a hash, and keeps what a person reads in its label
TABLE_NAME = "k3x9q2m7ab"
LABELS = {TABLE_NAME: "Table 5"}

PORTAL = "cloudprnt-provisional-portal@yopmail.com"
PORTAL_WITH_ROLES = "cloudprnt-provisional-portal-roles@yopmail.com"
DESK_WITHOUT_INVOICES = "cloudprnt-provisional-desk@yopmail.com"
CASHIER = "cloudprnt-provisional-cashier@yopmail.com"


def read_labels(doctype, label_field, name):
	return LABELS.get(name, name)


def chf(value):
	"""An amount as the site prints it (with or without its symbol, as the site is set)."""
	return fmt_money(value, currency="CHF").strip()


class Order(SimpleNamespace):
	"""A table order as the ticket reads it: attributes and .get(), like a Document."""

	def get(self, key, default=None):
		return getattr(self, key, default)


def order(**overrides):
	row = frappe._dict
	doc = Order(
		doctype="Sales Invoice",
		name="ACC-SINV-TEST-0003",
		docstatus=0,
		company="_No Such Company [test]",
		currency="CHF",
		pos_profile=PROFILE,
		owner="Administrator",
		restaurant_table=TABLE_NAME,
		is_takeaway=0,
		takeaway_number="",
		customer_name="Walk-in",
		discount_amount=0,
		net_total=108.98,
		grand_total=116.03,
		rounded_total=116.05,
		items=[
			row(
				item_code="STEAK",
				item_name="Steak [rare]",
				qty=2,
				rate=38.5,
				amount=77.0,
				# as POS Next keeps a line's options
				posa_item_modifiers='[{"group": "Cooking", "options": [{"name": "Medium", "price_adjustment": 0}]},'
				' {"group": "Side", "options": [{"name": "Fries", "price_adjustment": 0}, {"name": "Salad"}]}]',
				posa_special_instructions="No salt",
			),
			row(item_code="WINE", item_name="House red", qty=1, rate=8.0, amount=8.0),
			row(item_code="BREAD", item_name="Takeaway bread", qty=1.5, rate=20.69, amount=31.03),
		],
		taxes=[
			row(
				# the company does not exist here, so its abbreviation is unknown: the description carries none
				description="VAT 8.1%",
				account_head="22007 - VAT 8.1% - NSC",
				tax_amount=6.54,
				included_in_print_rate=1,
			),
			row(
				description="VAT 2.6%",
				account_head="22005 - VAT 2.6% - NSC",
				tax_amount=0.51,
				included_in_print_rate=1,
			),
			# the till's template has a row for every rate: one that charged nothing is not printed
			row(
				description="VAT 3.8%",
				account_head="22006 - VAT 3.8% - NSC",
				tax_amount=0,
				included_in_print_rate=1,
			),
		],
		# a payment already recorded on the draft (a guest paid part from their phone): never on this ticket
		payments=[row(mode_of_payment="Cash", amount=20.0)],
	)
	doc.__dict__.update(overrides)
	return doc


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


class RolledBack(FrappeTestCase):
	"""Whatever a test writes is rolled back: nothing of it stays on the site."""

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.addCleanup(frappe.set_user, "Administrator")
		self.addCleanup(frappe.db.rollback)
		commit = patch.object(frappe.db, "commit")
		commit.start()
		self.addCleanup(commit.stop)


class TestProvisionalTicketMarkup(FrappeTestCase):
	def markup(self, doc=None, **options):
		with patch("cloudprnt.labels.label_of", read_labels):
			return get_provisional_ticket_markup(doc or order(), **options)

	def test_a_table_order_prints_its_lines_each_vat_rate_and_the_rounded_total(self):
		markup = self.markup()
		self.assertIn("_No Such Company \\[test\\]", markup, "the brackets of a name are printed, not read as tags")
		self.assertIn(frappe._("PROVISIONAL TICKET"), markup)
		self.assertIn("Table 5", markup, "the table is named by its label")
		self.assertNotIn(TABLE_NAME, markup)
		self.assertIn(f"{frappe._('Date')}: {formatdate(nowdate())}", markup)
		# the lines: quantity, name, options and remarks, amount
		self.assertIn(f"[column: left 2 x Steak (rare); right {chf(77)}]", markup)
		self.assertNotIn("[rare]", markup, "a name never opens a tag")
		self.assertIn("   Cooking: Medium", markup, "options are read out of POS Next's JSON")
		self.assertIn("   Side: Fries, Salad", markup)
		self.assertIn("   No salt", markup)
		self.assertNotIn("price_adjustment", markup)
		self.assertIn(f"[column: left 1 x House red; right {chf(8)}]", markup)
		self.assertIn(f"[column: left 1.5 x Takeaway bread; right {chf(31.03)}]", markup)
		# the VAT of each rate as the invoice holds it, and its total rounded as the till rounds it
		self.assertIn(f"[column: left VAT 8.1%:; right {chf(6.54)}]", markup)
		self.assertIn(f"[column: left VAT 2.6%:; right {chf(0.51)}]", markup)
		self.assertNotIn("3.8%", markup, "a rate that charged nothing is not printed")
		self.assertNotIn("22007", markup, "the account number is dropped")
		self.assertNotIn(frappe._("Net Total"), markup, "prices include the tax: no net total line")
		self.assertIn(f"right {chf(116.05)}]", markup)
		self.assertNotIn(chf(116.03), markup, "the total is the rounded one")
		# it is not a receipt and says so
		self.assertIn(frappe._("THIS IS NOT A RECEIPT"), markup)
		self.assertNotIn("Cash", markup, "no payment on a provisional ticket")
		self.assertNotIn(chf(20), markup)
		self.assertNotIn("[barcode", markup, "no invoice barcode: it is not the invoice")
		self.assertTrue(markup.rstrip().endswith("[cut: feed; partial]"))

	def test_a_takeaway_order_is_named_by_its_number(self):
		markup = self.markup(order(restaurant_table=None, is_takeaway=1, takeaway_number="T-012"))
		self.assertIn(f"{frappe._('Takeaway')} T-012", markup)
		self.assertNotIn("Table 5", markup)

	def test_a_discount_and_a_tax_added_on_top_are_shown(self):
		doc = order(discount_amount=5.0)
		doc.taxes[0].included_in_print_rate = 0
		markup = self.markup(doc)
		self.assertIn(f"[column: left {frappe._('Discount')}:; right {chf(5)}]", markup)
		self.assertIn(f"[column: left {frappe._('Net Total')}:; right {chf(108.98)}]", markup)

	def test_without_rounding_the_grand_total_is_printed(self):
		markup = self.markup(order(rounded_total=0))
		self.assertIn(f"right {chf(116.03)}]", markup)

	def test_a_long_name_keeps_its_amount_on_the_next_line(self):
		doc = order()
		doc.items[1].item_name = "A very long name of a dish that does not fit on one line of the roll"
		markup = self.markup(doc)
		self.assertIn("1 x A very long name of a dish that does not fit on one line of the roll\n", markup)
		self.assertIn(f"[column: left ; right {chf(8)}]", markup)

	def test_the_rule_fits_the_paper(self):
		self.assertIn("-" * 32 + "\n", self.markup(paper_width="58mm"))
		self.assertNotIn("-" * 33, self.markup(paper_width="58mm"))
		self.assertIn("-" * 48, self.markup(paper_width=80))


class TestWhatIsPrinted(FrappeTestCase):
	def test_an_order_with_no_lines_is_refused_clearly(self):
		with self.assertRaises(frappe.ValidationError) as refused:
			provisional_ticket._check_order(order(items=[]))
		self.assertIn(frappe._("there is nothing to print"), str(refused.exception))

	def test_a_paid_order_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			provisional_ticket._check_order(order(docstatus=1))

	def test_a_sale_that_is_no_table_or_takeaway_order_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			provisional_ticket._check_order(order(restaurant_table=None, is_takeaway=0))

	def test_an_open_table_order_with_lines_is_printed(self):
		provisional_ticket._check_order(order())
		provisional_ticket._check_order(order(restaurant_table=None, is_takeaway=1, takeaway_number="T-1"))


class TestProvisionalTicketQueue(RolledBack):
	def setUp(self):
		super().setUp()
		frappe.cache().delete_keys("cloudprnt_")
		# The till's printer opens its drawer on every sale: a provisional ticket still never opens it
		self.printer = self.add_printer("_Test CloudPRNT provisional", PROFILE, open_drawer="Always")
		self.other = self.add_printer("_Test CloudPRNT other till", OTHER_PROFILE, open_drawer="Always")

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

	def jobs_of(self, name):
		return frappe.get_all(
			jobs.DOCTYPE,
			filters={"reference_doctype": "Sales Invoice", "reference_name": name},
			fields=["printer", "status", "open_drawer", "expires_at", "creation", "job_data", "job_token"],
		)

	def test_the_ticket_is_queued_for_the_tills_printer_expires_and_never_opens_the_drawer(self):
		doc = order()
		with (
			patch("cloudprnt.provisional_ticket._open_order", return_value=doc),
			patch("cloudprnt.labels.label_of", read_labels),
		):
			answer = print_provisional_ticket(doc.name)
		self.assertTrue(answer["success"])
		self.assertEqual(answer["printer"], "_Test CloudPRNT provisional")
		self.assertIn("_Test CloudPRNT provisional", answer["message"])
		[job] = self.jobs_of(doc.name)
		self.assertEqual(job.printer, self.printer, "the printer of the order's till")
		self.assertEqual(job.status, "Pending")
		self.assertIn(frappe._("THIS IS NOT A RECEIPT"), job.job_data)
		self.assertIsNotNone(job.expires_at, "it expires, like a receipt")
		self.assertLessEqual((job.expires_at - job.creation).total_seconds(), jobs.RECEIPT_VALID_MINUTES * 60 + 1)
		self.assertGreater((job.expires_at - job.creation).total_seconds(), 60)
		# no job asks for the drawer, and none is journaled as opened for it
		self.assertEqual(job.open_drawer or "", "")
		self.assertIsNone(jobs.drawer_for(frappe._dict(job)))
		self.assertEqual(
			frappe.db.count(jobs.DOCTYPE, {"reference_name": doc.name, "open_drawer": ("in", ("start", "end"))}), 0
		)
		self.assertEqual(frappe.db.count(drawer.DOCTYPE, {"print_job": job.job_token}), 0)
		self.assertEqual(frappe.db.count(jobs.DOCTYPE, {"printer": self.other}), 0, "the other till's printer gets nothing")

	def test_the_printer_the_till_names_is_the_one_it_prints_on(self):
		doc = order()
		with patch("cloudprnt.provisional_ticket._open_order", return_value=doc):
			print_provisional_ticket(doc.name, printer="_Test CloudPRNT other till")
		self.assertEqual([job.printer for job in self.jobs_of(doc.name)], [self.other])
		self.assertEqual([job.open_drawer or "" for job in self.jobs_of(doc.name)], [""])

	def test_a_till_without_a_printer_is_refused_and_nothing_is_queued(self):
		"""Never the site's default printer: it may stand in another room, or another restaurant."""
		doc = order(pos_profile="_Test CloudPRNT till without printer")
		with (
			patch("cloudprnt.provisional_ticket._open_order", return_value=doc),
			self.assertRaises(frappe.ValidationError) as refused,
		):
			print_provisional_ticket(doc.name)
		self.assertIn("_Test CloudPRNT till without printer", str(refused.exception))
		self.assertEqual(self.jobs_of(doc.name), [])


class TestWhoMayPrintIt(RolledBack):
	def test_a_portal_account_is_refused_whatever_its_roles(self):
		make_user(PORTAL, ["Customer"], "Website User")
		# a portal account given a role that reads invoices, by mistake: a role does not make it the till's staff
		make_user(PORTAL_WITH_ROLES, ["Accounts User"], "Website User")
		for user in (PORTAL, PORTAL_WITH_ROLES):
			with self.subTest(user=user), as_user(user), self.assertRaises(frappe.PermissionError):
				print_provisional_ticket("ACC-SINV-NO-SUCH-0001")

	def test_a_desk_user_who_may_not_read_invoices_is_refused(self):
		make_user(DESK_WITHOUT_INVOICES, ["Blogger"], "System User")
		with as_user(DESK_WITHOUT_INVOICES), self.assertRaises(frappe.PermissionError):
			print_provisional_ticket("ACC-SINV-NO-SUCH-0001")

	def test_a_name_that_is_not_a_name_is_refused(self):
		for name in ({"name": "ACC-SINV-0001"}, ["ACC-SINV-0001"], 7, ""):
			with self.subTest(name=name), self.assertRaises(frappe.DoesNotExistError):
				print_provisional_ticket(name)

	def test_the_staff_reaches_the_order(self):
		"""Past the guards, an order that does not exist is reported as such: nothing here is a refusal."""
		with self.assertRaises(frappe.DoesNotExistError):
			print_provisional_ticket("ACC-SINV-NO-SUCH-0001")

	def test_an_order_of_a_restaurant_the_user_may_not_read_is_refused(self):
		if not provisional_ticket._restaurants_known():
			raise unittest.SkipTest("POS Next with restaurants is not installed on this site")
		company = frappe.get_all("Company", limit=1, pluck="name")
		if not company:
			self.skipTest("The site has no company")

		def insert(doctype, **values):
			return frappe.get_doc({"doctype": doctype, **values}).insert(ignore_permissions=True).name

		ours, theirs = (
			insert("Restaurant", restaurant_name=f"_Test CloudPRNT Restaurant {key}", company=company[0], enable_tips=0)
			for key in ("A", "B")
		)
		# the same label in both restaurants, as two places of one owner may have
		our_table, their_table = (
			insert(
				"Restaurant Table",
				area=insert("Restaurant Area", restaurant=restaurant, area_name="_Test CloudPRNT Room"),
				table_name="_Test CloudPRNT T1",
			)
			for restaurant in (ours, theirs)
		)
		make_user(CASHIER, ["POSNext Cashier"], "System User")
		insert("User Permission", user=CASHIER, allow="Restaurant", for_value=ours)
		self.addCleanup(frappe.clear_cache, user=CASHIER)
		frappe.clear_cache(user=CASHIER)
		with as_user(CASHIER):
			with self.assertRaises(frappe.PermissionError):
				provisional_ticket._require_restaurant_of(order(restaurant_table=their_table))
			provisional_ticket._require_restaurant_of(order(restaurant_table=our_table))
			# a takeaway order belongs to the restaurant of the till that took it
			takeaway = order(restaurant_table=None, is_takeaway=1, pos_profile=OTHER_PROFILE)
			with patch("pos_next.restaurant.settings.restaurant_for_profile", return_value=theirs):
				with self.assertRaises(frappe.PermissionError):
					provisional_ticket._require_restaurant_of(takeaway)
			with patch("pos_next.restaurant.settings.restaurant_for_profile", return_value=ours):
				provisional_ticket._require_restaurant_of(takeaway)
