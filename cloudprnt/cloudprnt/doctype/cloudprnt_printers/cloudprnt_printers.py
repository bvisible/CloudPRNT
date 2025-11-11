# Copyright (c) 2024, Neoffice and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class CloudPRNTPrinters(Document):
    """
    CloudPRNT Printers DocType

    Manages printer configurations for CloudPRNT system.
    Printer status is updated automatically by cloudprnt_server.py when printers poll.
    """

    def __title(self):
        # Customize this to return the desired label
        return f"{self.label}"
