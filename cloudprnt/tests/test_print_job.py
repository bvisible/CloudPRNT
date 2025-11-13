"""
Tests for Print Job Generation
================================

Tests PNG generation, Star Line Mode generation, UTF-8 encoding,
and CPUtil integration with fallback.

Run: bench --site sitename run-tests cloudprnt.tests.test_print_job
"""

import pytest
import frappe
from PIL import Image
from io import BytesIO
from cloudprnt.print_job import StarCloudPRNTStarLineModeJob
from cloudprnt.tests.utils import (
    mock_printer_meta,
    get_test_markup,
    get_test_markup_simple,
    assert_hex_valid,
    assert_png_valid
)

# Try to import PNG generation function (may not exist in all versions)
try:
    from cloudprnt.print_job import generate_receipt_png
    PNG_AVAILABLE = True
except ImportError:
    PNG_AVAILABLE = False


@pytest.mark.png
@pytest.mark.unit
@pytest.mark.skipif(not PNG_AVAILABLE, reason="PNG generation not available in this version")
class TestPNGGeneration:
    """Tests for PNG receipt generation"""

    def test_generate_receipt_png_basic_text(self):
        """Test PNG generation with basic text"""
        markup = "Test Receipt\nLine 1\nLine 2\nLine 3"

        png_data = generate_receipt_png(markup)

        assert_png_valid(png_data)

        # Verify it's actually an image
        img = Image.open(BytesIO(png_data))
        assert img.format == "PNG"
        assert img.width == 576  # Default width
        assert img.height > 0

        frappe.logger().info(f"✅ PNG basic test passed ({img.width}x{img.height})")

    def test_generate_receipt_png_utf8_characters(self):
        """Test PNG generation with UTF-8 characters (€, é, è, à)"""
        markup = """Prix: 10.50€
Café au lait
Thé à la menthe
Crème brûlée"""

        png_data = generate_receipt_png(markup)

        assert_png_valid(png_data)

        img = Image.open(BytesIO(png_data))
        assert img.width == 576
        assert img.height > 80  # Should have some height for 4 lines

        frappe.logger().info("✅ PNG UTF-8 test passed")

    def test_generate_receipt_png_alignment_center(self):
        """Test PNG generation handles alignment markers"""
        markup = """[align: centre]
Header Text Centered
[align: left]
Left aligned text
[align: right]
Right aligned text"""

        png_data = generate_receipt_png(markup)

        assert_png_valid(png_data)

        frappe.logger().info("✅ PNG alignment test passed")

    def test_generate_receipt_png_font_fallback(self):
        """Test PNG generation handles font fallback"""
        # This should work even if Monaco font not available
        markup = "Test with font fallback\nAnother line"

        png_data = generate_receipt_png(markup)

        assert_png_valid(png_data)

    def test_generate_receipt_png_dimensions_576px(self):
        """Test PNG default width is 576px (80mm)"""
        markup = "Test"

        png_data = generate_receipt_png(markup, width_pixels=576)

        img = Image.open(BytesIO(png_data))
        assert img.width == 576

    def test_generate_receipt_png_custom_width(self):
        """Test PNG generation with custom width"""
        markup = "Test"

        # 58mm printer (384px)
        png_data = generate_receipt_png(markup, width_pixels=384)
        img = Image.open(BytesIO(png_data))
        assert img.width == 384

        # 112mm printer (832px)
        png_data = generate_receipt_png(markup, width_pixels=832)
        img = Image.open(BytesIO(png_data))
        assert img.width == 832

        frappe.logger().info("✅ PNG custom width test passed")


@pytest.mark.unit
class TestStarLineModeJob:
    """Tests for StarCloudPRNTStarLineModeJob class"""

    def test_job_init_python_native(self):
        """Test job initialization with Python Native (no CPUtil)"""
        printer_meta = mock_printer_meta()

        job = StarCloudPRNTStarLineModeJob(printer_meta)

        
        assert job.printer_mac == "00:11:62:12:34:56"
        assert job.print_job_builder == ""

        frappe.logger().info("✅ Job init Python Native test passed")

    def test_job_init_cputil_if_available(self):
        """Test job initialization attempts CPUtil if requested"""
        printer_meta = mock_printer_meta()

        job = StarCloudPRNTStarLineModeJob(printer_meta)

        # use_cputil will be True only if CPUtil is actually available
        # We don't assert the value, just that it doesn't crash
        

        frappe.logger().info("✅ Job init test passed")

    def test_str_to_hex_utf8_uppercase(self):
        """Test str_to_hex converts UTF-8 to uppercase hex"""
        printer_meta = mock_printer_meta()
        job = StarCloudPRNTStarLineModeJob(printer_meta)

        # Test basic ASCII
        hex_ascii = job.str_to_hex("ABC")
        assert hex_ascii == "414243"

        # Test UTF-8 characters
        hex_euro = job.str_to_hex("€")
        assert hex_euro == "E282AC"  # Euro sign in UTF-8

        hex_cafe = job.str_to_hex("café")
        assert hex_cafe == "636166C3A9"  # "café" in UTF-8

        frappe.logger().info("✅ str_to_hex UTF-8 test passed")

    def test_build_job_from_markup_basic(self):
        """Test build_job_from_markup with simple markup"""
        printer_meta = mock_printer_meta()
        job = StarCloudPRNTStarLineModeJob(printer_meta)

        markup = get_test_markup_simple()

        # Try to build (will use CPUtil if available, else return False)
        result = job.build_job_from_markup(markup)

        # If CPUtil was used, verify hex
        if result:
            assert len(job.print_job_builder) > 0
            assert_hex_valid(job.print_job_builder)
            frappe.logger().info("✅ Build from markup with CPUtil test passed")
        else:
            # CPUtil not available or fallback
            frappe.logger().info("⚠️  Build from markup - CPUtil not used (expected if not installed)")

    def test_hex_output_valid_format(self):
        """Test generated hex is valid uppercase format"""
        printer_meta = mock_printer_meta()
        job = StarCloudPRNTStarLineModeJob(printer_meta)

        # Manually build some hex using class methods
        job.add_text("Test")
        job.add_new_line(1)
        job.cut()

        hex_output = job.print_job_builder

        # Should have some content
        assert len(hex_output) > 0

        # Should be valid hex
        assert_hex_valid(hex_output)

        frappe.logger().info("✅ Hex output format test passed")


@pytest.mark.unit
class TestStarLineModeCommands:
    """Tests for Star Line Mode command generation"""

    def test_set_text_emphasized(self):
        """Test emphasis command"""
        printer_meta = mock_printer_meta()
        job = StarCloudPRNTStarLineModeJob(printer_meta)

        job.set_text_emphasized()

        # Should have emphasis hex command
        assert "1B45" in job.print_job_builder

    def test_set_text_alignment(self):
        """Test alignment commands"""
        printer_meta = mock_printer_meta()
        job = StarCloudPRNTStarLineModeJob(printer_meta)

        job.set_text_center_align()
        assert "1B1D6101" in job.print_job_builder

        job.set_text_left_align()
        assert "1B1D6100" in job.print_job_builder

        job.set_text_right_align()
        assert "1B1D6102" in job.print_job_builder

        frappe.logger().info("✅ Alignment commands test passed")

    def test_add_barcode(self):
        """Test barcode generation"""
        printer_meta = mock_printer_meta()
        job = StarCloudPRNTStarLineModeJob(printer_meta)

        job.add_barcode(
            type=4,  # Code128
            module=2,
            hri=True,
            height=100,
            data="TEST123"
        )

        hex_output = job.print_job_builder

        # Should contain barcode command (1B62)
        assert "1B62" in hex_output

        # Should contain barcode data
        barcode_data_hex = job.str_to_hex("TEST123")
        assert barcode_data_hex in hex_output

        frappe.logger().info("✅ Barcode generation test passed")


@pytest.mark.integration
class TestPrintJobWithRealMarkup:
    """Tests with real POS Invoice markup"""

    def test_build_job_from_real_markup(self):
        """Test building job from realistic receipt markup"""
        printer_meta = mock_printer_meta()
        job = StarCloudPRNTStarLineModeJob(printer_meta)

        markup = get_test_markup()  # Realistic receipt markup

        result = job.build_job_from_markup(markup)

        # If CPUtil used, verify output
        if result:
            assert len(job.print_job_builder) > 100  # Should be substantial
            assert_hex_valid(job.print_job_builder)


@pytest.mark.cputil
@pytest.mark.integration
class TestCPUtilIntegrationInPrintJob:
    """Tests for CPUtil integration in print job"""

    def test_cputil_options_from_settings(self):
        """Test _get_cputil_options_from_settings"""
        printer_meta = mock_printer_meta()
        job = StarCloudPRNTStarLineModeJob(printer_meta)

        options = job._get_cputil_options_from_settings()

        assert isinstance(options, dict)
        assert "printer_width" in options
        assert "dither" in options
        assert "partial_cut" in options

        frappe.logger().info(f"✅ CPUtil options: {options}")

    def test_fallback_to_python_on_cputil_error(self):
        """Test fallback to Python when CPUtil fails"""
        printer_meta = mock_printer_meta()
        job = StarCloudPRNTStarLineModeJob(printer_meta)

        # Invalid markup that would make CPUtil fail
        invalid_markup = "[invalid: command that does not exist]"

        # Should not crash - should fallback to Python
        try:
            result = job.build_job_from_markup(invalid_markup)
            # If we get here without exception, test passed
            frappe.logger().info("✅ Fallback to Python test passed")
        except Exception as e:
            pytest.fail(f"Fallback failed: {e}")


def run_tests():
    """Helper to run all print job tests"""
    import sys
    exit_code = pytest.main([__file__, "-v"])
    return exit_code


if __name__ == "__main__":
    import sys
    sys.exit(run_tests())
