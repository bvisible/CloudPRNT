# Copyright (c) 2024, Neoffice and Contributors
# See license.txt

from frappe.tests.utils import FrappeTestCase

# The test runner builds test records for every DocType a field links to, and on through what those link to: the
# printers' table and the default printer (CloudPRNT Printers) link to a till (POS Profile), which pulls ERPNext's
# whole chain of test companies, accounts and taxes that a clone of a live site does not have, and to the
# printer's account (User), which needs none. The tests create what they use.
test_ignore = ["CloudPRNT Printers", "POS Profile", "User"]


class TestCloudPRNTSettings(FrappeTestCase):
	pass
