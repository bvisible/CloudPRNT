"""
CloudPRNT Tests Configuration
==============================

Test-specific fixtures for cloudprnt/tests/ directory.
"""

import pytest
import frappe
from cloudprnt.tests.utils import (
    create_test_printer,
    remove_test_printer,
    create_test_invoice,
    delete_test_invoice,
    clear_test_print_queue,
    cleanup_all_test_data
)


@pytest.fixture(scope="function")
def test_printer(test_printer_mac):
    """
    Fixture to create and cleanup test printer

    Usage:
        def test_something(test_printer):
            # test_printer is the MAC address
            print(test_printer)  # "00:11:62:12:34:56"
    """
    # Create printer
    create_test_printer(test_printer_mac)

    yield test_printer_mac

    # Cleanup
    remove_test_printer(test_printer_mac)


@pytest.fixture(scope="function")
def test_invoice():
    """
    Fixture to create and cleanup test POS Invoice

    Usage:
        def test_something(test_invoice):
            # test_invoice is the invoice name
            print(test_invoice)  # "POS-INV-XXXXX"
    """
    # Create invoice
    invoice_name = create_test_invoice()

    yield invoice_name

    # Cleanup
    delete_test_invoice(invoice_name)


@pytest.fixture(scope="function", autouse=True)
def auto_cleanup_queue():
    """
    Automatically cleanup test print queue after each test

    Runs after EVERY test function automatically
    """
    yield

    # Cleanup after test
    clear_test_print_queue()


@pytest.fixture(scope="session", autouse=True)
def cleanup_on_exit():
    """
    Cleanup all test data at end of test session

    Runs once after all tests complete
    """
    yield

    # Cleanup at end of session
    try:
        cleanup_all_test_data()
        frappe.logger().info("✅ Test data cleanup completed")
    except Exception as e:
        frappe.logger().error(f"Cleanup error: {e}")


@pytest.fixture
def mock_cputil_available(monkeypatch):
    """
    Mock CPUtil as available for testing

    Usage:
        def test_something(mock_cputil_available):
            # CPUtil will appear available
    """
    def mock_is_available():
        return True

    monkeypatch.setattr(
        "cloudprnt.cputil_wrapper.is_cputil_available",
        mock_is_available
    )


@pytest.fixture
def mock_cputil_unavailable(monkeypatch):
    """
    Mock CPUtil as unavailable for testing

    Usage:
        def test_something(mock_cputil_unavailable):
            # CPUtil will appear unavailable
    """
    def mock_is_available():
        return False

    monkeypatch.setattr(
        "cloudprnt.cputil_wrapper.is_cputil_available",
        mock_is_available
    )
