"""
Root conftest.py for CloudPRNT Tests
=====================================

Provides global fixtures and configuration for pytest.
"""

import pytest
import frappe
import os


@pytest.fixture(scope="session", autouse=True)
def frappe_session():
    """
    Initialize Frappe session for all tests
    Automatically runs before any test session
    """
    # Initialize Frappe if not already initialized
    if not frappe.db:
        # Get site name from environment or use default
        site = os.environ.get("FRAPPE_SITE", "test_site")

        # Change to bench directory if we're in apps/cloudprnt
        original_dir = os.getcwd()
        bench_path = os.environ.get("FRAPPE_BENCH_PATH")

        # Try to find bench path by going up from current directory
        if not bench_path:
            current = os.getcwd()
            while current != '/':
                if os.path.exists(os.path.join(current, 'sites', 'apps.txt')):
                    bench_path = current
                    break
                current = os.path.dirname(current)

        # Change to bench directory if found
        if bench_path and os.path.exists(bench_path):
            os.chdir(bench_path)

        frappe.init(site=site)
        frappe.connect()

        # Restore original directory
        os.chdir(original_dir)

    yield

    # Cleanup after all tests
    if frappe.db:
        frappe.db.rollback()
        frappe.destroy()


@pytest.fixture
def test_printer_mac():
    """
    Fixture providing a test printer MAC address
    """
    return "00:11:62:12:34:56"


@pytest.fixture
def test_printer_mac_dots():
    """
    Fixture providing a test printer MAC address in dot notation
    """
    return "00.11.62.12.34.56"


@pytest.fixture
def test_invoice_data():
    """
    Fixture providing test invoice data
    """
    return {
        "doctype": "POS Invoice",
        "customer": "_Test Customer",
        "company": "_Test Company",
        "posting_date": "2025-01-13",
        "posting_time": "14:30:00",
        "currency": "CHF",
        "items": [
            {
                "item_code": "TEST-ITEM-001",
                "item_name": "Test Product 1",
                "qty": 2,
                "rate": 10.50,
                "amount": 21.00
            },
            {
                "item_code": "TEST-ITEM-002",
                "item_name": "Café au lait",  # UTF-8 test
                "qty": 1,
                "rate": 4.50,
                "amount": 4.50
            }
        ],
        "payments": [
            {
                "mode_of_payment": "Cash",
                "amount": 25.50
            }
        ]
    }


@pytest.fixture
def cleanup_print_queue():
    """
    Fixture to cleanup print queue after tests
    """
    yield

    # Cleanup after test
    try:
        frappe.db.sql("DELETE FROM `tabCloudPRNT Print Queue` WHERE job_token LIKE 'TEST-%'")
        frappe.db.commit()
    except:
        pass


@pytest.fixture
def cleanup_test_printers():
    """
    Fixture to cleanup test printers from settings
    """
    yield

    # Cleanup after test
    try:
        settings = frappe.get_single("CloudPRNT Settings")
        settings.printers = [
            p for p in settings.printers
            if not p.mac_address.startswith("00:11:62")
        ]
        settings.save()
        frappe.db.commit()
    except:
        pass


def pytest_configure(config):
    """
    Pytest hook - runs before test collection
    """
    # Register custom markers
    config.addinivalue_line(
        "markers", "unit: Unit tests (no external dependencies)"
    )
    config.addinivalue_line(
        "markers", "integration: Integration tests (requires Frappe)"
    )
    config.addinivalue_line(
        "markers", "slow: Slow tests (performance, stress tests)"
    )
    config.addinivalue_line(
        "markers", "mqtt: Requires MQTT broker"
    )
    config.addinivalue_line(
        "markers", "cputil: Requires CPUtil installed"
    )


def pytest_collection_modifyitems(config, items):
    """
    Pytest hook - modify test items after collection

    Automatically skip tests requiring external dependencies if not available
    """
    # Check if CPUtil is available
    try:
        from cloudprnt.cputil_wrapper import is_cputil_available
        cputil_available = is_cputil_available()
    except:
        cputil_available = False

    # Skip CPUtil tests if not available
    skip_cputil = pytest.mark.skip(reason="CPUtil not installed")
    for item in items:
        if "cputil" in item.keywords and not cputil_available:
            item.add_marker(skip_cputil)

    # Check if MQTT broker is available
    mqtt_available = os.environ.get("MQTT_BROKER_HOST") is not None
    skip_mqtt = pytest.mark.skip(reason="MQTT broker not configured")
    for item in items:
        if "mqtt" in item.keywords and not mqtt_available:
            item.add_marker(skip_mqtt)
