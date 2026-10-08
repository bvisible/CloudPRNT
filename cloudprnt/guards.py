"""Who may call the app's HTTP methods.

A plain @frappe.whitelist() method answers any signed-in account, a portal customer (Website User) with no
role included: Frappe checks no permission of its own. Every whitelisted method of this app therefore calls
`require` first, and tests/test_exposed_endpoints.py fails on one that does not.
"""

import frappe
from frappe import _


def require(doctype, ptype="read", name=None):
	"""Refuse the call unless the session holds `ptype` on `doctype`, or on the document `name`."""
	if not frappe.has_permission(doctype, ptype, doc=name):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
