"""The server fetches a job's images from the public web only, and cputil fetches nothing (audit of 06.11).

cputil downloaded the image of an `[image: url …]` tag itself, from the server, following redirects: an image link
of a test print, or a logo address of the settings, made the server request its own internal services. A local web
server stands in here for such an internal service, and for a logo on the web (its name is resolved to it by the
test, as a public host's would be). The client's administrator, who sets the logos and sends the test prints, plays
the acts.
"""

import io
import os
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

import frappe
from frappe.tests.roles import as_role
from frappe.tests.utils import FrappeTestCase
from PIL import Image

from cloudprnt import devices, images, render
from cloudprnt import print_queue_manager as jobs
from cloudprnt.cloudprnt.doctype.cloudprnt_settings import cloudprnt_settings
from cloudprnt.printers import SETTINGS

LOGO_HOST = "logo.example.test"


def _png():
	buffer = io.BytesIO()
	Image.new("L", (120, 40), 0).save(buffer, "PNG")
	return buffer.getvalue()


class _Service(BaseHTTPRequestHandler):
	hits = []

	def do_GET(self):
		self.hits.append(self.path)
		if self.path == "/redirect":
			self.send_response(302)
			self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/secret")
			self.end_headers()
			return
		body = _png()
		self.send_response(200)
		self.send_header("Content-Type", "image/png")
		self.send_header("Content-Length", str(len(body)))
		self.end_headers()
		self.wfile.write(body)

	def log_message(self, *args):
		pass


class TestWebImages(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.server = HTTPServer(("127.0.0.1", 0), _Service)
		cls.port = cls.server.server_port
		threading.Thread(target=cls.server.serve_forever, daemon=True).start()

	@classmethod
	def tearDownClass(cls):
		cls.server.shutdown()
		cls.server.server_close()
		super().tearDownClass()

	def setUp(self):
		_Service.hits.clear()
		frappe.cache().delete_keys("cloudprnt_image_refused_")
		self.folder = tempfile.mkdtemp(prefix="cloudprnt-test-")
		real = images.public_address
		# The logo's host is on the web; every other name is resolved and checked for real.
		self.resolve = patch.object(
			images, "public_address", lambda host, port: "127.0.0.1" if host == LOGO_HOST else real(host, port)
		)

	def tearDown(self):
		shutil.rmtree(self.folder, ignore_errors=True)

	def internal(self, path="/metadata"):
		return f"http://127.0.0.1:{self.port}{path}"

	def logo(self, path="/logo.png"):
		return f"http://{LOGO_HOST}:{self.port}{path}"

	def test_an_image_at_an_internal_address_is_never_requested(self):
		for address in (self.internal(), f"http://localhost:{self.port}/metadata", "http://169.254.169.254/latest/"):
			with self.subTest(address=address):
				markup = images.localise(f"[image: url {address}; width 60%]\nHello", self.folder)
				self.assertEqual(markup, "\nHello", "the image is left out, the job still prints")
		self.assertEqual(_Service.hits, [])

	def test_a_redirect_to_an_internal_address_is_not_followed(self):
		with self.resolve:
			markup = images.localise(f"[image: url {self.logo('/redirect')}; width 60%]\nHello", self.folder)
		self.assertEqual(markup, "\nHello")
		self.assertEqual(_Service.hits, ["/redirect"], "the internal address it points to is never requested")

	def test_a_file_on_the_server_is_not_printed(self):
		markup = images.localise("[image: url /etc/hostname; width 60%]\n[image: url file:///etc/hostname]\nHello", self.folder)
		self.assertEqual(markup, "\n\nHello")

	def test_a_logo_on_the_web_is_printed_from_a_downloaded_file(self):
		with self.resolve:
			markup = images.localise(f"[image: url {self.logo()}; width 60%; min-width 48mm]\nHello", self.folder)
		self.assertEqual(_Service.hits, ["/logo.png"])
		path = markup.split("url ", 1)[1].split(";", 1)[0]
		self.assertTrue(path.startswith(self.folder) and os.path.isfile(path), markup)
		self.assertTrue(markup.endswith("; width 60%; min-width 48mm]\nHello"))
		with Image.open(path) as image:
			self.assertEqual(image.format, "PNG")

	def test_the_company_logo_prepared_for_paper_stays(self):
		folder = frappe.get_site_path("private", "cloudprnt")
		os.makedirs(folder, exist_ok=True)
		path = os.path.abspath(os.path.join(folder, "logo-_test_cloudprnt.png"))
		with open(path, "wb") as handle:
			handle.write(_png())
		try:
			markup = f"[image: url {path}; width 60%]\nHello"
			self.assertEqual(images.localise(markup, self.folder), markup)
		finally:
			os.remove(path)

	@unittest.skipUnless(render.cputil_available(), "cputil cannot run here")
	def test_cputil_is_handed_files_only(self):
		# What is left of the job once its image is left out: the line the tag stood on, then the text.
		text_only = render.render("\nHello\n[cut: feed; partial]", "application/vnd.star.starprnt")
		blocked = render.render(f"[image: url {self.internal()}]\nHello\n[cut: feed; partial]", "application/vnd.star.starprnt")
		self.assertEqual(_Service.hits, [], "cputil requested the internal address itself")
		self.assertEqual(blocked, text_only)
		with self.resolve:
			with_logo = render.render(f"[image: url {self.logo()}; width 60%]\nHello\n[cut: feed; partial]", "application/vnd.star.starprnt")
		self.assertEqual(_Service.hits, ["/logo.png"], "the logo is requested once, by the server, not by cputil")
		self.assertGreater(len(with_logo), len(text_only), "the logo is printed")

	def test_a_test_print_takes_web_addresses_only(self):
		with as_role("client_admin"):
			credentials = cloudprnt_settings.pair_printer(label="_Test CloudPRNT images")
		row = credentials["printer"]
		try:
			with as_role("client_admin"):
				broken = cloudprnt_settings.test_print(row, image_link="https://example.com/a.png]\n[image: url /etc/hostname")
				sent = cloudprnt_settings.test_print(row, image_link="https://example.com/logo.png")
			self.assertFalse(broken["success"], "a link that closes the tag and opens another is refused")
			self.assertTrue(sent["success"])
			job = frappe.db.get_value(jobs.DOCTYPE, {"job_token": sent["job_token"]}, "job_data")
			self.assertIn("[image: url https://example.com/logo.png;", job)
		finally:
			frappe.set_user("Administrator")
			settings = frappe.get_doc(SETTINGS)
			settings.set("printers", [r for r in settings.printers if r.name != row])
			if settings.default_printer == row:
				settings.default_printer = None
			settings.save()
			frappe.db.delete(jobs.DOCTYPE, {"printer": row})
			frappe.delete_doc("User", devices.account_of(row), force=True, ignore_permissions=True)
			frappe.db.commit()
