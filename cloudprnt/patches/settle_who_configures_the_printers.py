# //// Neoffice — added file (no upstream equivalent).
"""Who configures the printers, and who only prints (neoffice-maintenance#853).

Every desk account (`Desk User`) could change and delete the CloudPRNT Settings - the printers, their pairing,
what each till prints - and the CloudPRNT Logs, while the client's administrator (`Admin`) could only read them.
Decided on 2026-11-11: the printers are configured by the till's managers (`POSNext Manager`, `Restaurant
Manager`), the client's administrator and the System Manager; a cashier reads them and prints, nothing more.
Nothing writes the CloudPRNT Logs: a desk account keeps reading them, it no longer writes nor deletes them.

The DocTypes' JSON says it for a new site. A site whose rules were customised (`Custom DocPerm`) ignores its
JSON, so the rules are set through add_permission / update_permission_property, which first copy the standard
rules over when there is no custom one yet. Idempotent; a role the site does not have is skipped.
"""

import frappe
from frappe.permissions import add_permission, setup_custom_perms, update_permission_property

SETTINGS = "CloudPRNT Settings"
LOGS = "CloudPRNT Logs"
DESK_USER = "Desk User"
CONFIGURERS = ("Admin", "POSNext Manager", "Restaurant Manager")
CONFIGURE = ("read", "write", "create", "print")
# What a desk account loses: everything that changes the document or hands it on.
WRITES = ("write", "create", "delete", "share", "email")


def execute():
	if frappe.db.exists("DocType", SETTINGS):
		for role in CONFIGURERS:
			if frappe.db.exists("Role", role):
				_give(SETTINGS, role, CONFIGURE)
		_take(SETTINGS, DESK_USER, WRITES)
	if frappe.db.exists("DocType", LOGS):
		_take(LOGS, DESK_USER, WRITES)


def _give(doctype, role, rights):
	try:
		setup_custom_perms(doctype)
		# add_permission says « Rule … already exists » for a rule there already: asked only when it is missing.
		if not frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0, "if_owner": 0}):
			add_permission(doctype, role, permlevel=0)
		for right in rights:
			update_permission_property(doctype, role, 0, right, 1, validate=False)
	except Exception as e:
		frappe.log_error(f"CloudPRNT right not granted to {role}", str(e))
	frappe.clear_cache(doctype=doctype)


def _take(doctype, role, rights):
	setup_custom_perms(doctype)
	for right in rights:
		update_permission_property(doctype, role, 0, right, 0, validate=False)
	frappe.clear_cache(doctype=doctype)
