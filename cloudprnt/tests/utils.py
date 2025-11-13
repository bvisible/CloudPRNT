"""
Test Utilities for CloudPRNT
=============================

Provides helper functions and utilities for creating test data.
"""

import frappe
import json
from datetime import datetime, timedelta


def create_test_printer(mac_address="00:11:62:12:34:56", label="Test Printer", use_mqtt=False):
    """
    Create a test printer in CloudPRNT Settings

    :param mac_address: Printer MAC address (with colons)
    :param label: Printer label
    :param use_mqtt: Enable MQTT for this printer
    :return: Printer MAC address
    """
    try:
        settings = frappe.get_single("CloudPRNT Settings")

        # Check if printer already exists
        existing = [p for p in settings.printers if p.mac_address == mac_address]
        if existing:
            return mac_address

        # Add new printer
        settings.append("printers", {
            "label": label,
            "mac_address": mac_address,
            "use_mqtt": 1 if use_mqtt else 0
        })
        settings.save()
        frappe.db.commit()

        return mac_address

    except Exception as e:
        frappe.log_error(f"Error creating test printer: {str(e)}", "create_test_printer")
        raise


def remove_test_printer(mac_address="00:11:62:12:34:56"):
    """
    Remove a test printer from CloudPRNT Settings

    :param mac_address: Printer MAC address to remove
    """
    try:
        settings = frappe.get_single("CloudPRNT Settings")
        settings.printers = [p for p in settings.printers if p.mac_address != mac_address]
        settings.save()
        frappe.db.commit()
    except Exception as e:
        frappe.log_error(f"Error removing test printer: {str(e)}", "remove_test_printer")


def create_test_invoice(customer="_Test Customer", company="_Test Company", items=None):
    """
    Create a test POS Invoice

    :param customer: Customer name
    :param company: Company name
    :param items: List of items (default: 2 test items)
    :return: Invoice name
    """
    if items is None:
        items = [
            {
                "item_code": "TEST-ITEM-001",
                "item_name": "Test Product 1",
                "qty": 2,
                "rate": 10.50,
                "uom": "Unit"
            },
            {
                "item_code": "TEST-ITEM-002",
                "item_name": "Café au lait",  # UTF-8 test
                "qty": 1,
                "rate": 4.50,
                "uom": "Unit"
            }
        ]

    try:
        invoice = frappe.get_doc({
            "doctype": "POS Invoice",
            "customer": customer,
            "company": company,
            "posting_date": datetime.now().strftime("%Y-%m-%d"),
            "posting_time": datetime.now().strftime("%H:%M:%S"),
            "currency": "CHF",
            "items": items,
            "payments": [
                {
                    "mode_of_payment": "Cash",
                    "amount": sum(item["qty"] * item["rate"] for item in items)
                }
            ]
        })

        invoice.insert(ignore_permissions=True)
        frappe.db.commit()

        return invoice.name

    except Exception as e:
        frappe.log_error(f"Error creating test invoice: {str(e)}", "create_test_invoice")
        raise


def delete_test_invoice(invoice_name):
    """
    Delete a test POS Invoice

    :param invoice_name: Name of invoice to delete
    """
    try:
        if frappe.db.exists("POS Invoice", invoice_name):
            frappe.delete_doc("POS Invoice", invoice_name, ignore_permissions=True, force=True)
            frappe.db.commit()
    except Exception as e:
        frappe.log_error(f"Error deleting test invoice: {str(e)}", "delete_test_invoice")


def create_test_print_job(job_token, printer_mac, invoice_name=None):
    """
    Create a test print job in the queue

    :param job_token: Unique job token
    :param printer_mac: Printer MAC address
    :param invoice_name: POS Invoice name (optional)
    :return: Job token
    """
    try:
        from cloudprnt.print_queue_manager import add_job_to_queue

        result = add_job_to_queue(
            job_token=job_token,
            printer_mac=printer_mac,
            invoice_name=invoice_name
        )

        return job_token if result.get("success") else None

    except Exception as e:
        frappe.log_error(f"Error creating test print job: {str(e)}", "create_test_print_job")
        raise


def clear_test_print_queue(printer_mac=None):
    """
    Clear all test print jobs from queue

    :param printer_mac: Clear only for specific printer (optional)
    """
    try:
        if printer_mac:
            frappe.db.sql("""
                DELETE FROM `tabCloudPRNT Print Queue`
                WHERE printer_mac = %s
                AND job_token LIKE 'TEST-%'
            """, (printer_mac.upper(),))
        else:
            frappe.db.sql("""
                DELETE FROM `tabCloudPRNT Print Queue`
                WHERE job_token LIKE 'TEST-%'
            """)

        frappe.db.commit()

    except Exception as e:
        frappe.log_error(f"Error clearing test print queue: {str(e)}", "clear_test_print_queue")


def get_test_markup():
    """
    Get sample Star Document Markup for testing

    :return: Star Markup string
    """
    return """[align: centre][font: a]
Test Receipt
Test Company
123 Test Street
1000 Test City

Invoice: TEST-INV-001
Date: 13-01-2025
Time: 14:30:00
Customer: Test Customer
Currency: CHF

[align: centre]
------------------------------------------------
[align: left]

Test Product 1 (TEST-ITEM-001)
2 x CHF 10.50                           CHF 21.00

Café au lait (TEST-ITEM-002)
1 x CHF 4.50                             CHF 4.50

[align: centre]
------------------------------------------------
[align: left]

Grand Total:                            CHF 25.50
Cash:                                   CHF 25.50

[align: centre]
[barcode: type code128; data TEST-INV-001; height 15mm; module 2; hri]

[cut: feed; partial]
"""


def get_test_markup_simple():
    """
    Get simple Star Document Markup for testing

    :return: Simple markup string
    """
    return """[align: centre]
Test Receipt
[align: left]
Line 1
Line 2
Line 3
[cut]
"""


def assert_hex_valid(hex_string):
    """
    Assert that a string is valid hex (uppercase, even length)

    :param hex_string: String to validate
    :raises AssertionError: If not valid hex
    """
    assert isinstance(hex_string, str), "Hex must be string"
    assert len(hex_string) > 0, "Hex must not be empty"
    assert len(hex_string) % 2 == 0, "Hex must have even length"
    assert all(c in '0123456789ABCDEF' for c in hex_string), "Hex must contain only 0-9A-F"


def assert_png_valid(png_data):
    """
    Assert that binary data is a valid PNG image

    :param png_data: Binary PNG data
    :raises AssertionError: If not valid PNG
    """
    assert isinstance(png_data, bytes), "PNG data must be bytes"
    assert len(png_data) > 0, "PNG data must not be empty"
    assert png_data[:8] == b'\x89PNG\r\n\x1a\n', "PNG signature invalid"


def assert_mac_address_valid(mac_address):
    """
    Assert that a MAC address is valid (colon notation)

    :param mac_address: MAC address string
    :raises AssertionError: If not valid MAC address
    """
    import re
    pattern = r'^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$'
    assert re.match(pattern, mac_address), f"Invalid MAC address format: {mac_address}"


def cleanup_all_test_data():
    """
    Clean up all test data from database

    WARNING: Only use in test environment!
    """
    try:
        # Clear test print jobs
        frappe.db.sql("DELETE FROM `tabCloudPRNT Print Queue` WHERE job_token LIKE 'TEST-%'")

        # Clear test logs
        frappe.db.sql("DELETE FROM `tabCloudPRNT Logs` WHERE document_link LIKE 'TEST-%'")

        # Clear test invoices
        frappe.db.sql("DELETE FROM `tabPOS Invoice` WHERE name LIKE 'TEST-%'")

        # Remove test printers from settings
        settings = frappe.get_single("CloudPRNT Settings")
        settings.printers = [p for p in settings.printers if not p.mac_address.startswith("00:11:62")]
        settings.save()

        frappe.db.commit()

    except Exception as e:
        frappe.log_error(f"Error cleaning up test data: {str(e)}", "cleanup_all_test_data")


def mock_printer_meta(mac_address="00:11:62:12:34:56"):
    """
    Create mock printer metadata dict

    :param mac_address: Printer MAC address
    :return: Printer metadata dict
    """
    return {
        "printerMAC": mac_address,
        "statusCode": "200 OK",
        "clientType": 0,
        "clientVersion": "1.0.0"
    }
