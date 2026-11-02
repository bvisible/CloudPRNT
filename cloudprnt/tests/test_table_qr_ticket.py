"""The QR ticket of a table: a small paper to stick on the table or hand to the guests (NeoResto lot 1, task 53).

The till's QR button shows the code that opens the guest ordering page of a table; its Print button asks POS Next, which
names the moment (the hook `pos_next_table_qr_print`) and hands this app the table and the link IT worked out from the
guest token. This app prints: the table's name in large letters, a line telling the guest to scan to order, the QR code of
the link, and the link in small characters under it. The suite builds the printers in memory and runs the real markup,
the real claim of the double tap and the real guard on the link, over a fake queue.
"""

import re
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from cloudprnt import print_queue_manager as jobs
from cloudprnt import table_qr_ticket
from cloudprnt.table_qr_ticket import (
	LINE_CHARS,
	PRINT_DOTS,
	QR_WINDOW_SECONDS,
	get_table_qr_ticket_markup,
	on_table_qr_print,
	qr_cell,
)
from cloudprnt.tests.hostile_text import CLEAN, HOSTILE, unsafe_characters

LINK = "https://shop.example.test/pos/guest/AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-AbCdE"
PROFILE = "Restaurant till"
TABLE = "tbl-0001"


def printer(label="Receipt printer", profile=PROFILE, online=1, width=80):
	return frappe._dict(
		name=label, label=label, pos_profile=profile, online=online, paper_width=width, preparation_station=""
	)


class FakeQueue:
	"""What `jobs.enqueue` does for a ticket, kept in memory."""

	def __init__(self):
		self.jobs = []
		self.fail = False

	def enqueue(self, row, markup, **values):
		if self.fail:
			raise RuntimeError("the queue is down")
		token = f"tok-{len(self.jobs) + 1}"
		self.jobs.append(frappe._dict(printer=row.label, markup=markup, token=token, **values))
		return {"success": True, "job_token": token, "queue_position": len(self.jobs)}


class QrTicketCase(FrappeTestCase):
	def setUp(self):
		super().setUp()
		self.queue = FakeQueue()
		self.printers = [printer()]
		self.labels = {TABLE: "Table 5"}
		self.patch("cloudprnt.table_qr_ticket.jobs.enqueue", self.queue.enqueue)
		self.patch("cloudprnt.api.all_printers", lambda: self.printers)
		self.patch("cloudprnt.labels.label_of", lambda doctype, field, name: self.labels.get(name, name))
		self.patch("cloudprnt.table_qr_ticket.default_paper_width", lambda: 80)
		self.forget_claims()
		self.addCleanup(self.forget_claims)

	def patch(self, target, replacement):
		patcher = patch(target, replacement)
		patcher.start()
		self.addCleanup(patcher.stop)

	def forget_claims(self):
		frappe.cache().delete_keys("cloudprnt_table_qr:")

	def tap(self, table=TABLE, link=LINK, profile=PROFILE):
		return on_table_qr_print(table, link, profile)

	@property
	def markup(self):
		return self.queue.jobs[0].markup


def qr_tags(markup):
	return re.findall(r"\[barcode: type qr;[^\]]*\]", markup)


def qr_data(markup):
	"""The data of the one QR tag of `markup`, between its quotes."""
	(tag,) = qr_tags(markup)
	return re.search(r'data "((?:[^"\\]|\\.)*)"', tag).group(1)


class TestWhatThePaperHolds(QrTicketCase):
	def test_the_paper_reads_the_table_then_the_scan_line_then_the_code_then_the_link_and_is_cut(self):
		answer = self.tap()

		self.assertEqual(len(self.queue.jobs), 1)
		markup = self.markup
		self.assertTrue(answer["printed"])
		self.assertIn("Table 5", markup)
		(tag,) = qr_tags(markup)
		table_at, scan_at, qr_at = markup.index("Table 5"), markup.index("Scan to order"), markup.index(tag)
		link_at = markup.index(LINK, qr_at + len(tag))
		self.assertLess(table_at, scan_at)
		self.assertLess(scan_at, qr_at)
		self.assertLess(qr_at, link_at)
		self.assertTrue(markup.rstrip().endswith("[cut: feed; partial]"))

	def test_the_code_is_the_link_the_app_was_handed_and_the_link_is_printed_again_in_small_characters(self):
		self.tap()

		self.assertEqual(qr_data(self.markup), LINK)
		(tag,) = qr_tags(self.markup)
		after = self.markup[self.markup.index(tag) + len(tag) :]
		# small characters (font B), then back to the default font
		self.assertRegex(after, r"\[font: b\]\s*" + re.escape(LINK) + r"\s*\[font\]")

	def test_everything_is_centred_and_the_table_name_is_big(self):
		self.tap()

		head = self.markup.split("\n")[0]
		self.assertEqual(head, "[align: centre]")
		(big,) = re.findall(r"\[magnify: width (\d); height (\d)\]\[bold: on\]Table 5", self.markup)
		self.assertGreaterEqual(int(big[0]), 3)

	def test_a_long_table_name_gets_a_narrower_type_so_that_it_stays_on_one_line(self):
		for width in (58, 80, 112):
			for name in ("5", "Table 5", "Terrace table 12", "The big round table by the window"):
				with self.subTest(width=width, name=name):
					markup = get_table_qr_ticket_markup(name, LINK, paper_width=width)
					wide, _high = re.search(r"\[magnify: width (\d); height (\d)\]\[bold: on\]", markup).groups()
					self.assertTrue(int(wide) * len(name) <= LINE_CHARS[width] or int(wide) == 1)

	def test_the_scan_line_is_the_translated_one(self):
		with patch("cloudprnt.table_qr_ticket._", lambda text, *args, **kwargs: f"<<{text}>>"):
			markup = get_table_qr_ticket_markup("Table 5", LINK)

		self.assertIn("<<Scan to order>>", markup)

	def test_nothing_on_the_paper_but_the_table_the_scan_line_and_the_link(self):
		self.tap()

		text = re.sub(r"\[[^\]]*\]", "", self.markup)
		words = [line.strip() for line in text.splitlines() if line.strip()]
		self.assertEqual(words, ["Table 5", "Scan to order", LINK])


class TestTheCodeIsReadableFromATable(QrTicketCase):
	def test_the_code_is_drawn_with_the_biggest_cell_the_paper_holds(self):
		for width in (58, 80, 112):
			with self.subTest(width=width):
				cell = qr_cell(LINK, width)
				self.assertTrue(3 <= cell <= 8)
				# a link of this length is a version-5 code of 37 modules, and the paper holds it
				self.assertLessEqual(37 * cell, PRINT_DOTS[width])
				# at 203 dpi a cell of 6 dots or more makes a code of 3 cm or more, which a phone reads from a table
				self.assertGreaterEqual(cell, 6)

	def test_a_longer_link_gets_a_smaller_cell_never_a_code_wider_than_the_paper(self):
		links = [LINK + "a" * extra for extra in (0, 40, 80, 120, 200)]
		for width in (58, 80, 112):
			cells = [qr_cell(link, width) for link in links]
			with self.subTest(width=width):
				self.assertEqual(cells, sorted(cells, reverse=True))
				for link, cell in zip(links, cells, strict=True):
					modules = table_qr_ticket.modules_of(link)
					self.assertLessEqual(modules * cell, PRINT_DOTS[width])

	def test_the_tag_carries_the_cell_and_a_medium_error_correction(self):
		self.tap()

		(tag,) = qr_tags(self.markup)
		self.assertIn(f"cell {qr_cell(LINK, 80)}", tag)
		self.assertIn("error-correction medium", tag)

	def test_the_paper_of_the_printer_is_the_one_the_code_is_sized_for(self):
		self.printers = [printer(width=58)]
		self.tap()

		(tag,) = qr_tags(self.markup)
		self.assertIn(f"cell {qr_cell(LINK, 58)}", tag)

	def test_a_printer_with_no_paper_width_takes_the_default_of_the_settings(self):
		self.printers = [printer(width=None)]
		self.patch("cloudprnt.table_qr_ticket.default_paper_width", lambda: 112)
		self.tap()

		(tag,) = qr_tags(self.markup)
		self.assertIn(f"cell {qr_cell(LINK, 112)}", tag)


class TestWhereItPrints(QrTicketCase):
	def test_it_prints_on_the_receipt_printer_of_the_till_and_says_where_and_how_to_follow_it(self):
		self.printers = [printer("Other", profile="Another till"), printer("Receipt printer", online=0)]

		answer = self.tap()

		self.assertEqual([j.printer for j in self.queue.jobs], ["Receipt printer"])
		self.assertEqual(answer["printer"], "Receipt printer")
		self.assertIs(answer["printer_online"], False)
		self.assertEqual(answer["job_token"], "tok-1")
		self.assertEqual(answer["valid_minutes"], jobs.RECEIPT_VALID_MINUTES)
		self.assertIs(answer["duplicate"], False)

	def test_a_till_with_no_printer_is_told_so_and_nothing_is_queued(self):
		self.printers = [printer("Other", profile="Another till"), printer("Kitchen", profile=None)]

		answer = self.tap()

		self.assertEqual(self.queue.jobs, [])
		self.assertIs(answer["printed"], False)
		self.assertIs(answer["no_printer"], True)

	def test_a_till_with_no_printer_leaves_no_claim_so_that_a_printer_added_a_moment_later_prints(self):
		self.printers = []
		self.tap()
		self.printers = [printer()]

		answer = self.tap()

		self.assertTrue(answer["printed"])
		self.assertEqual(len(self.queue.jobs), 1)

	def test_a_paper_that_the_printer_did_not_take_in_time_is_not_printed_by_surprise_later(self):
		self.tap()

		job = self.queue.jobs[0]
		self.assertEqual(job.valid_seconds, jobs.RECEIPT_VALID_MINUTES * 60)

	def test_the_ticket_is_no_receipt_and_never_opens_the_cash_drawer_or_names_a_document(self):
		self.tap()

		job = self.queue.jobs[0]
		self.assertNotIn("open_drawer", job)
		self.assertNotIn("reference_doctype", job)
		self.assertNotIn("kitchen_rows", job)


class TestADoubleTapPrintsOnce(QrTicketCase):
	def test_the_same_table_asked_for_twice_in_a_few_seconds_prints_once_and_names_the_first_job(self):
		first = self.tap()
		second = self.tap()

		self.assertEqual(len(self.queue.jobs), 1)
		self.assertIs(first["duplicate"], False)
		self.assertIs(second["duplicate"], True)
		self.assertIs(second["printed"], True)
		self.assertEqual(second["job_token"], first["job_token"])
		self.assertEqual(second["printer"], first["printer"])

	def test_the_claim_expires_with_the_window(self):
		self.tap()

		cache = frappe.cache()
		key = cache.make_key(table_qr_ticket.claim_key(PROFILE, TABLE, LINK))
		self.assertTrue(0 < cache.ttl(key) <= QR_WINDOW_SECONDS)

	def test_once_the_window_is_over_the_same_table_prints_again(self):
		self.tap()
		self.forget_claims()

		self.tap()

		self.assertEqual(len(self.queue.jobs), 2)

	def test_another_table_or_another_link_is_another_paper(self):
		self.labels["tbl-0002"] = "Table 6"
		self.tap()
		self.tap(table="tbl-0002")
		self.tap(link=LINK + "x")

		self.assertEqual(len(self.queue.jobs), 3)

	def test_a_tap_that_fails_to_queue_is_not_remembered_so_the_next_one_tries_again(self):
		self.queue.fail = True
		with self.assertRaises(RuntimeError):
			self.tap()
		self.queue.fail = False

		answer = self.tap()

		self.assertTrue(answer["printed"])
		self.assertIs(answer["duplicate"], False)
		self.assertEqual(len(self.queue.jobs), 1)

	def test_a_second_tap_while_the_first_is_still_queueing_is_told_it_is_on_its_way(self):
		cache = frappe.cache()
		claim = cache.make_key(table_qr_ticket.claim_key(PROFILE, TABLE, LINK))
		cache.set(claim, "pending", ex=QR_WINDOW_SECONDS)

		answer = self.tap()

		self.assertEqual(self.queue.jobs, [])
		self.assertIs(answer["printed"], True)
		self.assertIs(answer["duplicate"], True)
		self.assertIsNone(answer["job_token"])


class TestNothingTheBrowserCouldHaveInjectedAndNothingUnsafe(QrTicketCase):
	def test_a_table_name_with_control_characters_is_made_safe_like_the_other_tickets_do(self):
		self.labels[TABLE] = HOSTILE

		self.tap()

		self.assertEqual(unsafe_characters(self.markup), [])
		self.assertIn(CLEAN, self.markup)

	def test_the_markup_characters_of_a_table_name_are_escaped_so_that_it_cannot_add_a_tag(self):
		self.labels[TABLE] = "T[cut: feed]\\5"

		self.tap()

		self.assertIn("T\\[cut: feed\\]\\\\5", self.markup)
		self.assertEqual(len(re.findall(r"(?<!\\)\[cut", self.markup)), 1)

	def test_a_link_that_is_not_a_web_address_prints_nothing(self):
		for link in (
			"javascript:alert(1)",
			"file:///etc/passwd",
			"//shop.example.test/pos/guest/x",
			"https:///pos/guest/x",
			"https://shop.example.test/pos/guest/a b",
			"https://shop.example.test/pos/guest/a\nb",
			"https://shop.example.test/pos/guest/a\x07b",
			"https://shop.example.test/pos/guest/a\u202eb\u200bc",
			"https://shop.example.test/" + "a" * 400,
			"",
			None,
			42,
			["https://shop.example.test/"],
		):
			with self.subTest(link=link), self.assertRaises(frappe.ValidationError):
				self.tap(link=link)
			self.assertEqual(self.queue.jobs, [])

	def test_a_link_cannot_break_out_of_the_tag_or_add_one(self):
		self.tap(link='https://shop.example.test/p?a="x";b=[cut:feed]\\')

		data = qr_data(self.markup)
		self.assertNotRegex(data, r'["\[\];\\]')
		self.assertEqual(data, "https://shop.example.test/p?a=%22x%22%3Bb=%5Bcut:feed%5D%5C")
		self.assertEqual(len(qr_tags(self.markup)), 1)
		self.assertEqual(self.markup.count("[cut"), 1)
		self.assertEqual(unsafe_characters(self.markup), [])
		# the small line under the code says what the code says
		self.assertIn(f"[font: b]\n{data}\n[font]", self.markup)

	def test_a_letter_outside_ascii_is_written_as_a_percent_escape_in_the_code_and_in_the_small_line(self):
		self.tap(link="https://shop.example.test/caf\u00e9")

		self.assertEqual(qr_data(self.markup), "https://shop.example.test/caf%C3%A9")
		self.assertIn("[font: b]\nhttps://shop.example.test/caf%C3%A9\n[font]", self.markup)
		self.assertEqual(unsafe_characters(self.markup), [])


class TestTheAppListensToPosNext(FrappeTestCase):
	def test_the_hook_is_declared_and_answers_a_function_that_exists(self):
		from cloudprnt import hooks

		self.assertEqual(hooks.pos_next_table_qr_print, ["cloudprnt.table_qr_ticket.on_table_qr_print"])
		self.assertIs(frappe.get_attr(hooks.pos_next_table_qr_print[0]), on_table_qr_print)

	def test_the_listener_is_no_endpoint_a_browser_could_call(self):
		self.assertNotIn(on_table_qr_print, frappe.whitelisted)


class TestTheFrenchIsInTheCatalogue(FrappeTestCase):
	"""The words on the paper and the refusal the till shows are translated (PO only, never a CSV), and the template is in step."""

	WORDS = (
		("Scan to order", "Scannez pour commander"),
		("The ordering link of this table is not a web address", "Le lien de commande de cette table n'est pas une adresse web"),
	)

	def test_each_word_has_its_french_entry_and_its_place_in_the_template(self):
		locale = Path(table_qr_ticket.__file__).resolve().parent / "locale"
		french = (locale / "fr.po").read_text(encoding="utf-8")
		template = (locale / "main.pot").read_text(encoding="utf-8")
		for english, translated in self.WORDS:
			with self.subTest(english):
				self.assertIn(f'msgid "{english}"\nmsgstr "{translated}"\n', french)
				self.assertIn(f'msgid "{english}"\nmsgstr ""\n', template)
