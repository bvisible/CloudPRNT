"""The account each printer signs in with.

The CloudPRNT protocol is open to anonymous callers, and anyone who could name a printer's MAC address used to
be handed its pending receipts. A printer now proves who it is: each one gets an account of its own (a Website
User with the "CloudPRNT Device" role and nothing else), and the printer sends that account's API key and
secret as the user name and password of its CloudPRNT settings, which a Star printer sends as HTTP Basic
authentication. Frappe signs the request in as that account, and the protocol only answers a printer signed in
as the account of its own row. The account counts for no user quota (Website User) and opens no desk.

The account of a printer row is always the one its row's name gives, printer-<row>@cloudprnt.invalid, and this
module never touches another: credentials are issued, renewed and withdrawn on that account only, whatever the
row's device_user says, and only on a portal account. A row's device_user is written by the server alone
(CloudPRNTSettings.validate drops what a save sends for it on a new row). Audit of 06.11.
"""

import frappe
from frappe import _

from cloudprnt.printers import PRINTER, SETTINGS, get_printer, update_printer

ROLE = "CloudPRNT Device"
DOMAIN = "cloudprnt.invalid"
PROTOCOL_PATH = "/api/method/cloudprnt.protocol.serve"


def ensure_role():
	"""The role printer accounts hold (after_migrate)."""
	if not frappe.db.exists("Role", ROLE):
		frappe.get_doc({"doctype": "Role", "role_name": ROLE, "desk_access": 0}).insert(ignore_permissions=True)


def server_url():
	return frappe.utils.get_url(PROTOCOL_PATH)


def account_of(printer_name):
	"""The account printer row `printer_name` signs in with: the only account this module creates or changes."""
	return f"printer-{printer_name}@{DOMAIN}".lower()


def is_account_of(user, printer_name):
	"""Whether `user` is the account of printer row `printer_name`."""
	return bool(user) and bool(printer_name) and str(user).lower() == account_of(printer_name)


def issue_credentials(printer_name):
	"""Create or renew the account of printer row `printer_name` and return what the printer is configured
	with: {"url", "user", "password"}. Renewing replaces the secret: the printer stops printing until it is
	configured with the new one."""
	row = get_printer(printer_name)
	if not row:
		frappe.throw(_("CloudPRNT printer {0} not found").format(printer_name))
	ensure_role()

	# The row's own account, never the one its device_user names: that field is not the caller's to choose.
	email = account_of(row.name)
	if frappe.db.exists("User", email):
		user = frappe.get_doc("User", email)
		if user.user_type != "Website User":
			# Only a portal account is turned into a printer: a desk account is never stripped of its roles here.
			frappe.throw(_("{0} is not a printer account").format(email), frappe.PermissionError)
	else:
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": "CloudPRNT",
				"last_name": (row.label or row.name)[:100],
				"user_type": "Website User",
				"send_welcome_email": 0,
				"enabled": 1,
			}
		)
		user.flags.skip_drive_setup = True
		user.flags.no_welcome_mail = True
		user.insert(ignore_permissions=True)

	# Apps add roles to new accounts (LMS Student, a portal default): a printer holds its own role only.
	user.set("roles", [])
	user.append("roles", {"role": ROLE})
	user.enabled = 1
	user.api_key = frappe.generate_hash(length=15)
	secret = frappe.generate_hash(length=15)
	user.api_secret = secret
	user.flags.ignore_permissions = True
	user.save(ignore_permissions=True)

	update_printer(row.name, {"device_user": user.name})
	return {"url": server_url(), "user": user.api_key, "password": secret}


def revoke_credentials(printer_name):
	"""The printer of row `printer_name` can no longer sign in (its account is disabled, its key cleared)."""
	row = get_printer(printer_name)
	if row and row.device_user:
		_disable(row.device_user, row.name)


def disable_accounts_of_removed_printers(previous_rows, current_names):
	"""Called when the settings are saved: a printer row removed takes its accounts with it (its sign-in here, and
	its account on the broker of instant printing)."""
	removed = [row for row in previous_rows if row.name not in current_names]
	for row in removed:
		if row.device_user:
			_disable(row.device_user, row.name)
	if removed:
		from cloudprnt import broker

		broker.forget(removed)


def _disable(user, printer_name):
	"""Disable `user`, when it is the account of printer row `printer_name` and nobody else's: a row that names
	another account (a person's, Administrator) never disables it."""
	from frappe.utils.password import remove_encrypted_password

	if not is_account_of(user, printer_name):
		return
	if frappe.db.exists("User", user) and frappe.db.get_value("User", user, "user_type") == "Website User":
		frappe.db.set_value("User", user, {"enabled": 0, "api_key": None})
		remove_encrypted_password("User", user, "api_secret")


def is_device_user(user=None):
	user = user or frappe.session.user
	return bool(user) and user != "Guest" and bool(frappe.db.exists(PRINTER, {"parent": SETTINGS, "device_user": user}))
