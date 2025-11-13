"""
Tests for CPUtil Integration
==============================

Test suite pour l'intégration de CPUtil dans CloudPRNT.
Tests la détection, conversion, fallback et performance.

Usage:
    cd ~/frappe-bench
    bench --site sitename run-tests cloudprnt.tests.test_cputil_integration
"""

import frappe
import unittest
import time
from cloudprnt.cputil_wrapper import (
    get_cputil_path,
    is_cputil_available,
    get_supported_input_types,
    convert_markup_to_starline,
    check_cputil_status
)
from cloudprnt.print_job import StarCloudPRNTStarLineModeJob


class TestCPUtilDetection(unittest.TestCase):
    """Tests for CPUtil detection and availability"""

    def test_01_get_cputil_path(self):
        """Test CPUtil path detection"""
        path = get_cputil_path()

        if path:
            self.assertIsInstance(path, str)
            self.assertTrue(len(path) > 0)
            frappe.logger().info(f"CPUtil found at: {path}")
        else:
            frappe.logger().warning("CPUtil not found - install tests will be skipped")

    def test_02_is_cputil_available(self):
        """Test CPUtil availability check"""
        available = is_cputil_available()
        self.assertIsInstance(available, bool)

        if available:
            frappe.logger().info("✅ CPUtil is available")
        else:
            frappe.logger().warning("❌ CPUtil is not available")

    def test_03_check_cputil_status(self):
        """Test CPUtil status check API"""
        status = check_cputil_status()

        self.assertIsInstance(status, dict)
        self.assertIn('available', status)
        self.assertIn('status', status)
        self.assertIn('message', status)

        frappe.logger().info(f"CPUtil Status: {status['status']}")
        frappe.logger().info(f"Message: {status['message']}")


class TestCPUtilConversion(unittest.TestCase):
    """Tests for CPUtil conversion functionality"""

    @classmethod
    def setUpClass(cls):
        """Check if CPUtil is available before running tests"""
        cls.cputil_available = is_cputil_available()
        if not cls.cputil_available:
            frappe.logger().warning("CPUtil not available - conversion tests will be skipped")

    def setUp(self):
        """Skip tests if CPUtil not available"""
        if not self.cputil_available:
            self.skipTest("CPUtil not installed")

    def test_01_simple_markup_conversion(self):
        """Test basic Star Document Markup conversion"""
        markup = "[align: centre]Test Receipt[align: left]\nLine 1\nLine 2\n[cut]"

        result = convert_markup_to_starline(markup)

        self.assertIsNotNone(result)
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)
        self.assertTrue(all(c in '0123456789ABCDEF' for c in result), "Result should be hex")

        frappe.logger().info(f"Converted {len(markup)} chars of markup to {len(result)} hex chars")

    def test_02_conversion_with_options(self):
        """Test conversion with various options"""
        markup = "Test with options\n[cut]"

        options = {
            'printer_width': 3,
            'dither': True,
            'partial_cut': True,
        }

        result = convert_markup_to_starline(markup, options)

        self.assertIsNotNone(result)
        self.assertTrue(len(result) > 0)

        frappe.logger().info(f"Conversion with options successful: {len(result)} hex chars")

    def test_03_utf8_characters(self):
        """Test UTF-8 characters (€, é, è, etc.)"""
        markup = "Prix: 10.50€\nCafé au lait\nThé à la menthe\n[cut]"

        result = convert_markup_to_starline(markup)

        self.assertIsNotNone(result)
        self.assertTrue(len(result) > 0)

        frappe.logger().info("UTF-8 conversion successful")

    def test_04_barcode_markup(self):
        """Test barcode markup"""
        markup = "[barcode: type code128; data TEST123; height 15mm; module 2; hri]\n[cut]"

        result = convert_markup_to_starline(markup)

        self.assertIsNotNone(result)
        self.assertTrue(len(result) > 0)

        frappe.logger().info("Barcode markup conversion successful")


class TestPrintJobIntegration(unittest.TestCase):
    """Tests for StarCloudPRNTStarLineModeJob with CPUtil"""

    @classmethod
    def setUpClass(cls):
        cls.cputil_available = is_cputil_available()

    def test_01_job_init_with_cputil(self):
        """Test job initialization with CPUtil enabled"""
        printer_meta = {'printerMAC': '00:11:62:12:34:56'}

        # Force CPUtil
        job = StarCloudPRNTStarLineModeJob(printer_meta, use_cputil=True)

        if self.cputil_available:
            self.assertTrue(job.use_cputil)
            frappe.logger().info("Job initialized with CPUtil")
        else:
            self.assertFalse(job.use_cputil)
            frappe.logger().info("Job initialized - CPUtil not available")

    def test_02_job_init_python_native(self):
        """Test job initialization with Python Native (CPUtil disabled)"""
        printer_meta = {'printerMAC': '00:11:62:12:34:56'}

        # Force Python Native
        job = StarCloudPRNTStarLineModeJob(printer_meta, use_cputil=False)

        self.assertFalse(job.use_cputil)
        frappe.logger().info("Job initialized with Python Native")

    def test_03_job_build_from_markup(self):
        """Test building job from markup with auto-selection"""
        if not self.cputil_available:
            self.skipTest("CPUtil not available")

        printer_meta = {'printerMAC': '00:11:62:12:34:56'}
        job = StarCloudPRNTStarLineModeJob(printer_meta, use_cputil=True)

        markup = "[align: centre]Test Receipt[align: left]\nLine 1\n[cut]"

        result = job.build_job_from_markup(markup)

        if result:
            self.assertTrue(len(job.print_job_builder) > 0)
            frappe.logger().info(f"✅ Job built with CPUtil: {len(job.print_job_builder)} hex chars")
        else:
            frappe.logger().warning("Job build returned False (Python fallback)")


class TestCPUtilFallback(unittest.TestCase):
    """Tests for CPUtil fallback mechanism"""

    @classmethod
    def setUpClass(cls):
        cls.cputil_available = is_cputil_available()

    def test_01_fallback_on_invalid_markup(self):
        """Test fallback to Python when markup is invalid"""
        if not self.cputil_available:
            self.skipTest("CPUtil not available")

        printer_meta = {'printerMAC': '00:11:62:12:34:56'}
        job = StarCloudPRNTStarLineModeJob(printer_meta, use_cputil=True)

        # Invalid markup should trigger fallback
        invalid_markup = "[invalid: command that does not exist]\n[cut]"

        # Should not crash - fallback to Python
        try:
            result = job.build_job_from_markup(invalid_markup)
            frappe.logger().info("Fallback mechanism tested - no crash on invalid markup")
        except Exception as e:
            self.fail(f"Fallback failed: {e}")

    def test_02_fallback_when_cputil_disabled(self):
        """Test that Python Native works when CPUtil disabled"""
        printer_meta = {'printerMAC': '00:11:62:12:34:56'}
        job = StarCloudPRNTStarLineModeJob(printer_meta, use_cputil=False)

        # Should use Python Native
        self.assertFalse(job.use_cputil)

        frappe.logger().info("Python Native mode verified")


class TestCPUtilPerformance(unittest.TestCase):
    """Performance tests for CPUtil"""

    @classmethod
    def setUpClass(cls):
        cls.cputil_available = is_cputil_available()

    def setUp(self):
        if not self.cputil_available:
            self.skipTest("CPUtil not available")

    def test_01_simple_conversion_performance(self):
        """Test that simple conversion is < 500ms"""
        markup = "[align: centre]Performance Test[align: left]\n" + "Line\n" * 10 + "[cut]"

        start_time = time.time()
        result = convert_markup_to_starline(markup)
        elapsed_time = (time.time() - start_time) * 1000  # Convert to ms

        self.assertIsNotNone(result)
        self.assertLess(elapsed_time, 500, f"Conversion took {elapsed_time:.2f}ms (should be < 500ms)")

        frappe.logger().info(f"⚡ Conversion time: {elapsed_time:.2f}ms")

    def test_02_large_receipt_performance(self):
        """Test performance with large receipt (50 lines)"""
        lines = []
        for i in range(50):
            lines.append(f"Item {i}: Product Name Long Description Here - Price: €{10+i}.99")

        markup = "[align: centre]Large Receipt[align: left]\n" + "\n".join(lines) + "\n[cut]"

        start_time = time.time()
        result = convert_markup_to_starline(markup)
        elapsed_time = (time.time() - start_time) * 1000

        self.assertIsNotNone(result)
        self.assertLess(elapsed_time, 1000, f"Large receipt took {elapsed_time:.2f}ms (should be < 1000ms)")

        frappe.logger().info(f"⚡ Large receipt time: {elapsed_time:.2f}ms")


class TestCPUtilWithRealInvoice(unittest.TestCase):
    """Tests with real POS Invoice (if available)"""

    @classmethod
    def setUpClass(cls):
        cls.cputil_available = is_cputil_available()

    def setUp(self):
        if not self.cputil_available:
            self.skipTest("CPUtil not available")

    def test_01_real_invoice_conversion(self):
        """Test with real POS Invoice markup (if exists)"""
        # Try to get a real POS Invoice
        invoices = frappe.get_all("POS Invoice", limit=1)

        if not invoices:
            self.skipTest("No POS Invoice found for testing")

        invoice_name = invoices[0].name

        try:
            from cloudprnt.pos_invoice_markup import get_pos_invoice_markup

            markup = get_pos_invoice_markup(invoice_name)

            self.assertIsNotNone(markup)
            self.assertTrue(len(markup) > 0)

            # Convert with CPUtil
            result = convert_markup_to_starline(markup)

            self.assertIsNotNone(result)
            self.assertTrue(len(result) > 100, "Invoice should produce substantial output")

            frappe.logger().info(f"✅ Real invoice converted: {invoice_name}")
            frappe.logger().info(f"   Markup: {len(markup)} chars → Hex: {len(result)} chars")

        except Exception as e:
            self.fail(f"Real invoice conversion failed: {e}")


def run_tests():
    """Helper function to run all tests"""
    import sys

    # Run tests
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add all test classes
    suite.addTests(loader.loadTestsFromTestCase(TestCPUtilDetection))
    suite.addTests(loader.loadTestsFromTestCase(TestCPUtilConversion))
    suite.addTests(loader.loadTestsFromTestCase(TestPrintJobIntegration))
    suite.addTests(loader.loadTestsFromTestCase(TestCPUtilFallback))
    suite.addTests(loader.loadTestsFromTestCase(TestCPUtilPerformance))
    suite.addTests(loader.loadTestsFromTestCase(TestCPUtilWithRealInvoice))

    # Run
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Return exit code
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(run_tests())
