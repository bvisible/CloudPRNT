# Copyright (c) 2026, Neoffice and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class CashDrawerOpening(Document):
	"""One opening of a cash drawer, written by cloudprnt.drawer; the desk only reads it."""
