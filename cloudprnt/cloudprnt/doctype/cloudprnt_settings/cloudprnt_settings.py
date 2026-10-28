# Copyright (c) 2023, Neoffice and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from cloudprnt import devices, guards
from cloudprnt import print_queue_manager as jobs
from cloudprnt.printers import SERVER_FIELDS, all_printers, find_printer, normalize_mac

SETUP_SCRIPT = "/assets/cloudprnt/star_printer_setup.py"


class CloudPRNTSettings(Document):
	def validate(self):
		for field in ("header_logo_url", "footer_logo_url"):
			value = (self.get(field) or "").strip()
			if value and not value.startswith(("http://", "https://")):
				frappe.throw(_("{0} must start with http:// or https://").format(_(self.meta.get_label(field))))
		self._keep_what_printers_reported()
		self._check_printers()

	def on_update(self):
		devices.disable_accounts_of_removed_printers(self._rows_before_save, {row.name for row in self.printers})

	def _keep_what_printers_reported(self):
		"""The form carries the fields printers write (status, identity, capabilities) as it loaded them:
		the stored values win, so saving a form opened a while ago does not wipe what a printer reported."""
		self._rows_before_save = all_printers()
		stored = {row.name: row for row in self._rows_before_save}
		for row in self.printers:
			saved = stored.get(row.name)
			if not saved:
				continue
			for field in SERVER_FIELDS:
				if field == "mac_address" and not saved.get(field):
					continue  # set by hand before the printer ever signed in
				row.set(field, saved.get(field))

	def _check_printers(self):
		macs, profiles = {}, {}
		for row in self.printers:
			if row.mac_address:
				mac = normalize_mac(row.mac_address)
				if not mac:
					frappe.throw(_("Row {0}: {1} is not a MAC address").format(row.idx, row.mac_address))
				if mac in macs:
					frappe.throw(_("Rows {0} and {1} have the same MAC address").format(macs[mac], row.idx))
				macs[mac] = row.idx
				row.mac_address = mac
			if row.pos_profile and row.auto_print:
				if row.pos_profile in profiles:
					frappe.throw(
						_("Rows {0} and {1} both print the receipts of {2}").format(
							profiles[row.pos_profile], row.idx, row.pos_profile
						)
					)
				profiles[row.pos_profile] = row.idx


@frappe.whitelist()
def pair_printer(printer=None, label=None):
	"""Issue the credentials a printer signs in with: for printer row `printer` (renewing them: the old ones
	stop working), or for a new printer row named `label`. Returns what the printer is configured with, and
	the command that configures it from a computer on its network."""
	guards.require("CloudPRNT Settings", "write")
	if not printer:
		label = (label or "").strip()
		if not label:
			frappe.throw(_("Give the printer a label"))
		settings = frappe.get_doc("CloudPRNT Settings")
		row = settings.append("printers", {"label": label})
		if not settings.default_printer:
			settings.save()
			settings.default_printer = row.name
		settings.save()
		printer = row.name
	else:
		printer = find_printer(printer).name
	credentials = devices.issue_credentials(printer)
	credentials["printer"] = printer
	credentials["setup_command"] = setup_command(credentials)
	return credentials


def setup_command(credentials):
	"""The command that configures a Star printer from a computer on its network (public/star_printer_setup.py)."""
	script = frappe.utils.get_url(SETUP_SCRIPT)
	return (
		f"curl -fsSLO {script} && python3 star_printer_setup.py"
		f" --url '{credentials['url']}' --user '{credentials['user']}' --password '{credentials['password']}'"
	)


@frappe.whitelist()
def test_print(printer, test_text=None, image_link=None):
	"""Print a test ticket on `printer`: text in every style the receipts use, a QR code, an image if given."""
	guards.require("CloudPRNT Settings", "write")
	image_link = (image_link or "").strip()
	if image_link and not image_link.lower().startswith(("http://", "https://")):
		return {"success": False, "message": _("The image link must start with http:// or https://")}
	row = find_printer(printer)
	text = (test_text or "").strip() or _("CloudPRNT print test")
	lines = ["[align: centre]", f"[magnify: width 2; height 2]{_('Print test')}[magnify]", ""]
	if image_link:
		lines += [f"[image: url {image_link}; width 60%; min-width 48mm]", ""]
	lines += [
		_escape(text),
		"",
		"[align: left]",
		f"[column: left {_('Printer')}; right {_param(row.label)}]",
		f"[column: left MAC; right {_param(row.mac_address or '-')}]",
		f"[column: left {_('Date')}; right {now_datetime().strftime('%d.%m.%Y %H:%M:%S')}]",
		f"[bold: on]{_('Bold')}[bold: off] éàèùç ÄÖÜ œ € CHF",
		"[align: centre]",
		f"[barcode: type qr; data {_param(frappe.utils.get_url())}; module 5]",
		"[cut: feed; partial]",
	]
	result = jobs.enqueue(row, "\n".join(lines), token=f"test-{frappe.generate_hash(length=12)}")
	return {"success": True, "message": _("Test print sent to {0}").format(row.label), **result}


def _escape(text):
	return str(text).replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _param(text):
	return str(text or "").replace(";", ",").replace("[", "(").replace("]", ")")
