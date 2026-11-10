"""Star's cputil is fetched from Star and pinned by sha256: the public repository no longer ships it (#2096).

Star's licence for the prebuilt binary does not let us redistribute it. These tests build a small archive shaped
like Star's (the same member path), pin it in place of Star's sums, and never reach the network: a fetch is the
function the test passes.
"""

import hashlib
import io
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock

from cloudprnt import cputil_install, render

FAKE_BINARY = b"\x7fELF fake cputil for the tests"


def fake_archive(binary=FAKE_BINARY, member=cputil_install.MEMBER):
	buffer = io.BytesIO()
	with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
		info = tarfile.TarInfo(member)
		info.size = len(binary)
		info.mode = 0o755
		archive.addfile(info, io.BytesIO(binary))
	return buffer.getvalue()


class TestCputilComesFromStar(unittest.TestCase):
	def setUp(self):
		self.folder = tempfile.mkdtemp(prefix="cputil_install_")
		self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
		self.target = os.path.join(self.folder, "bin", "cputil")
		self.archive = fake_archive()
		pins = mock.patch.multiple(
			cputil_install,
			ARCHIVE_SHA256=hashlib.sha256(self.archive).hexdigest(),
			BINARY_SHA256=hashlib.sha256(FAKE_BINARY).hexdigest(),
		)
		pins.start()
		self.addCleanup(pins.stop)
		log = mock.patch.object(cputil_install.frappe, "log_error")
		self.log_error = log.start()
		self.addCleanup(log.stop)

	def test_the_pinned_archive_installs_an_executable_binary(self):
		result = cputil_install.ensure_cputil(self.target, fetch=lambda: self.archive)

		self.assertEqual(result, "installed")
		with open(self.target, "rb") as handle:
			self.assertEqual(handle.read(), FAKE_BINARY)
		self.assertTrue(os.access(self.target, os.X_OK))
		self.assertEqual(os.listdir(os.path.dirname(self.target)), ["cputil"], "a partial file was left behind")

	def test_a_binary_already_pinned_is_kept_and_nothing_is_fetched(self):
		cputil_install.ensure_cputil(self.target, fetch=lambda: self.archive)
		fetch = mock.Mock(side_effect=AssertionError("fetched although the pinned binary is in place"))

		self.assertEqual(cputil_install.ensure_cputil(self.target, fetch=fetch), "present")
		fetch.assert_not_called()

	def test_an_archive_that_is_not_the_pinned_one_changes_nothing(self):
		for archive in (fake_archive(b"something else"), b"not an archive"):
			with self.subTest(size=len(archive)):
				result = cputil_install.ensure_cputil(self.target, fetch=lambda archive=archive: archive)
				self.assertTrue(result.startswith("failed"), result)
				self.assertFalse(os.path.exists(self.target))
		self.assertTrue(self.log_error.called)

	def test_a_binary_that_is_not_the_pinned_build_is_replaced(self):
		os.makedirs(os.path.dirname(self.target))
		with open(self.target, "wb") as handle:
			handle.write(b"an older build")

		self.assertEqual(cputil_install.ensure_cputil(self.target, fetch=lambda: self.archive), "installed")
		with open(self.target, "rb") as handle:
			self.assertEqual(handle.read(), FAKE_BINARY)

	def test_a_download_that_fails_is_logged_and_printing_goes_on(self):
		def unreachable():
			raise OSError("network unreachable")

		result = cputil_install.ensure_cputil(self.target, fetch=unreachable)

		self.assertEqual(result, "failed: network unreachable")
		self.assertFalse(os.path.exists(self.target))
		title = self.log_error.call_args.args[0]
		self.assertLessEqual(len(title), 140)

	def test_the_installer_puts_the_binary_where_render_runs_it(self):
		self.assertEqual(cputil_install.ensure_cputil.__defaults__[0], render.CPUTIL)

	def test_the_install_and_every_migrate_fetch_it(self):
		from cloudprnt import hooks

		self.assertIn("cloudprnt.cputil_install.install", hooks.after_install)
		self.assertIn("cloudprnt.cputil_install.install", hooks.after_migrate)

	def test_the_repository_does_not_ship_stars_binary(self):
		root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
		try:
			tracked = subprocess.run(
				["git", "-C", root, "ls-files", "cloudprnt/bin"], capture_output=True, text=True, timeout=30
			)
		except (OSError, subprocess.TimeoutExpired):
			self.skipTest("git is not available here")
		if tracked.returncode != 0:
			self.skipTest("not a git checkout")
		self.assertEqual(tracked.stdout.strip(), "", "Star's binary is tracked again; its licence forbids it")


if __name__ == "__main__":
	unittest.main()
