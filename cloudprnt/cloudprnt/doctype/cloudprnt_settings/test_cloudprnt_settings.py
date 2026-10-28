# Copyright (c) 2024, Neoffice and Contributors
# See license.txt

from frappe.tests.utils import FrappeTestCase

# The test runner builds test records for every DocType a field links to, through the printers' table: a till
# (POS Profile) pulls ERPNext's whole chain of test companies, accounts and taxes, which a clone of a live site
# does not have, and the printer's account (User) needs none. The tests create what they use.
test_ignore = ["POS Profile", "User"]


class TestCloudPRNTSettings(FrappeTestCase):
	pass
