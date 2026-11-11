# //// Neoffice — added file (no upstream equivalent).
"""Who configures the printers, and who only prints (neoffice-maintenance#853).

A cashier reads the CloudPRNT Settings and does not change them; the till's manager and the client's administrator
do; no desk account writes nor deletes the CloudPRNT Logs. Checked on the rules of the DocTypes (a new site) and on
a site whose rules were customised before the decision (the patch).
"""

import json
from pathlib import Path

import frappe
from frappe.permissions import setup_custom_perms, update_permission_property
from frappe.tests.roles import as_role
from frappe.tests.utils import FrappeTestCase

from cloudprnt.patches import settle_who_configures_the_printers as patch

SETTINGS = "CloudPRNT Settings"
LOGS = "CloudPRNT Logs"
ADMIN_ACCOUNT = "_test_853_admin@example.invalid"
# The roles of the personas and of the client's administrator, made when the site of the test has none (the CI of
# this app installs no till app): rolled back with the test.
ROLES = ("POSNext Cashier", "POSNext Manager", "Retail", "Admin")


def _doctype_rules(doctype):
	folder = doctype.lower().replace(" ", "_")
	path = Path(frappe.get_app_path("cloudprnt", "cloudprnt", "doctype", folder, f"{folder}.json"))
	return {row["role"]: row for row in json.loads(path.read_text())["permissions"]}


def _forget_rules():
	frappe.set_user("Administrator")
	for doctype in (SETTINGS, LOGS):
		frappe.clear_cache(doctype=doctype)


class TestSettingsRights(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		# Registered before the rollback of FrappeTestCase, so it runs after it: the cached rules are the site's again.
		cls.addClassCleanup(_forget_rules)
		super().setUpClass()
		frappe.set_user("Administrator")
		for role in ROLES:
			if not frappe.db.exists("Role", role):
				frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(ignore_permissions=True)
		if not frappe.db.exists("User", ADMIN_ACCOUNT):
			user = frappe.new_doc("User")
			user.update({"email": ADMIN_ACCOUNT, "first_name": "_853 admin", "send_welcome_email": 0})
			user.flags.skip_drive_setup = True
			user.insert(ignore_permissions=True)
		admin = frappe.get_doc("User", ADMIN_ACCOUNT)
		admin.set("roles", [{"role": "Admin"}])
		admin.save(ignore_permissions=True)
		# A site customised before the decision: every desk account writes and deletes both DocTypes.
		for doctype in (SETTINGS, LOGS):
			setup_custom_perms(doctype)
			if not frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": "Desk User", "permlevel": 0}):
				frappe.permissions.add_permission(doctype, "Desk User", 0)
			for right in ("read", "write", "create", "delete"):
				update_permission_property(doctype, "Desk User", 0, right, 1, validate=False)
			frappe.clear_cache(doctype=doctype)
		patch.execute()

	def test_a_cashier_reads_the_printers_and_does_not_configure_them(self):
		with as_role("cashier"):
			self.assertTrue(frappe.has_permission(SETTINGS, "read"))
			self.assertFalse(frappe.has_permission(SETTINGS, "write"))
			self.assertFalse(frappe.has_permission(SETTINGS, "delete"))

	def test_the_till_manager_and_the_administrator_configure_them(self):
		with as_role("till_manager"):
			self.assertTrue(frappe.has_permission(SETTINGS, "write"))
		with as_role(ADMIN_ACCOUNT):
			self.assertTrue(frappe.has_permission(SETTINGS, "write"))

	def test_no_desk_account_writes_the_logs(self):
		with as_role("cashier"):
			self.assertFalse(frappe.has_permission(LOGS, "write"))
			self.assertFalse(frappe.has_permission(LOGS, "delete"))
			self.assertFalse(frappe.has_permission(LOGS, "create"))

	def test_the_patch_runs_twice_without_changing_anything(self):
		def rules():
			return sorted(
				frappe.get_all(
					"Custom DocPerm",
					filters={"parent": ["in", (SETTINGS, LOGS)]},
					fields=["parent", "role", "permlevel", "read", "write", "create", "delete", "share", "email"],
				),
				key=lambda r: (r.parent, r.role, r.permlevel),
			)

		before = rules()
		patch.execute()
		self.assertEqual(rules(), before)

	def test_a_new_site_has_the_same_rules_from_the_doctypes(self):
		settings = _doctype_rules(SETTINGS)
		self.assertFalse(settings["Desk User"].get("write"))
		self.assertTrue(settings["Desk User"].get("read"))
		for role in ("Admin", "POSNext Manager", "Restaurant Manager", "System Manager"):
			with self.subTest(role=role):
				self.assertTrue(settings[role].get("write"))
		logs = _doctype_rules(LOGS)
		for right in ("write", "create", "delete"):
			with self.subTest(right=right):
				self.assertFalse(logs["Desk User"].get(right))
