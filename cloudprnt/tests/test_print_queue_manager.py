"""
Tests for CloudPRNT Print Queue Manager (Database)
===================================================

Tests the database-backed print queue system used in production.
Critical for multi-process job handling.

Run: bench --site sitename run-tests cloudprnt.tests.test_print_queue_manager
"""

import pytest
import frappe
import time
from cloudprnt.print_queue_manager import (
    add_job_to_queue,
    get_next_job,
    mark_job_fetched,
    mark_job_printed,
    get_queue_position,
    get_queue_status,
    clear_queue
)
from cloudprnt.tests.utils import clear_test_print_queue


@pytest.mark.queue
@pytest.mark.integration
class TestAddJobToQueue:
    """Tests for adding jobs to queue"""

    def setup_method(self):
        """Setup before each test"""
        clear_test_print_queue()

    def teardown_method(self):
        """Cleanup after each test"""
        clear_test_print_queue()

    def test_add_job_creates_record(self):
        """Test adding job creates database record"""
        result = add_job_to_queue(
            job_token="TEST-ADD-001",
            printer_mac="00:11:62:12:34:56",
            invoice_name="POS-INV-001"
        )

        assert result["success"] == True
        assert result["job_token"] == "TEST-ADD-001"
        assert result["queue_position"] == 1

        # Verify in database
        job = frappe.db.get_value(
            "CloudPRNT Print Queue",
            {"job_token": "TEST-ADD-001"},
            ["printer_mac", "status", "invoice_name"],
            as_dict=True
        )
        assert job is not None
        assert job.printer_mac == "00:11:62:12:34:56"
        assert job.status == "Pending"
        assert job.invoice_name == "POS-INV-001"

        frappe.logger().info("✅ Add job creates record test passed")

    def test_add_job_with_media_types(self):
        """Test adding job with custom media types"""
        result = add_job_to_queue(
            job_token="TEST-ADD-002",
            printer_mac="00:11:62:12:34:56",
            media_types=["image/png", "application/vnd.star.line"]
        )

        assert result["success"] == True

        # Verify media types stored
        job = frappe.get_doc("CloudPRNT Print Queue", {"job_token": "TEST-ADD-002"})
        import json
        media_types = json.loads(job.media_types)
        assert "image/png" in media_types

    def test_add_job_normalizes_mac_uppercase(self):
        """Test MAC address is normalized to uppercase"""
        result = add_job_to_queue(
            job_token="TEST-ADD-003",
            printer_mac="aa:bb:cc:dd:ee:ff"  # lowercase
        )

        job = frappe.db.get_value(
            "CloudPRNT Print Queue",
            {"job_token": "TEST-ADD-003"},
            "printer_mac"
        )
        assert job == "AA:BB:CC:DD:EE:FF"  # Should be uppercase


@pytest.mark.queue
@pytest.mark.integration
class TestGetNextJob:
    """Tests for retrieving next job from queue"""

    def setup_method(self):
        """Setup before each test"""
        clear_test_print_queue()

    def teardown_method(self):
        """Cleanup after each test"""
        clear_test_print_queue()

    def test_get_next_job_returns_oldest(self):
        """Test get_next_job returns oldest pending job"""
        # Add 3 jobs
        add_job_to_queue("TEST-GET-001", "00:11:62:12:34:56")
        time.sleep(0.1)  # Ensure different timestamps
        add_job_to_queue("TEST-GET-002", "00:11:62:12:34:56")
        time.sleep(0.1)
        add_job_to_queue("TEST-GET-003", "00:11:62:12:34:56")

        # Get next job - should be first one
        job = get_next_job("00:11:62:12:34:56")

        assert job is not None
        assert job["token"] == "TEST-GET-001"
        assert job["printer_mac"] == "00:11:62:12:34:56"

        frappe.logger().info("✅ Get next job returns oldest test passed")

    def test_get_next_job_empty_queue(self):
        """Test get_next_job returns None for empty queue"""
        job = get_next_job("00:11:62:12:34:56")

        assert job is None

    def test_get_next_job_skips_other_printers(self):
        """Test get_next_job only returns jobs for specific printer"""
        # Add jobs for different printers
        add_job_to_queue("TEST-P1-001", "00:11:62:11:11:11")
        add_job_to_queue("TEST-P2-001", "00:11:62:22:22:22")
        add_job_to_queue("TEST-P3-001", "00:11:62:33:33:33")

        # Get job for P2
        job = get_next_job("00:11:62:22:22:22")

        assert job is not None
        assert job["token"] == "TEST-P2-001"

        # Verify P1 and P3 jobs still pending
        p1_job = get_next_job("00:11:62:11:11:11")
        p3_job = get_next_job("00:11:62:33:33:33")

        assert p1_job["token"] == "TEST-P1-001"
        assert p3_job["token"] == "TEST-P3-001"

        frappe.logger().info("✅ Queue isolation test passed")


@pytest.mark.queue
@pytest.mark.integration
class TestMarkJobPrinted:
    """Tests for marking jobs as printed"""

    def setup_method(self):
        """Setup before each test"""
        clear_test_print_queue()

    def teardown_method(self):
        """Cleanup after each test"""
        clear_test_print_queue()

    def test_mark_job_printed_removes_from_queue(self):
        """Test marking job as printed removes it from queue"""
        add_job_to_queue("TEST-MARK-001", "00:11:62:12:34:56")

        # Verify job exists
        job_before = get_next_job("00:11:62:12:34:56")
        assert job_before is not None

        # Mark as printed
        result = mark_job_printed("TEST-MARK-001")

        assert result["success"] == True

        # Verify job removed
        job_after = get_next_job("00:11:62:12:34:56")
        assert job_after is None

        frappe.logger().info("✅ Mark job printed test passed")

    def test_mark_job_printed_invalid_token(self):
        """Test marking non-existent job as printed"""
        result = mark_job_printed("INVALID-TOKEN")

        assert result["success"] == False
        assert "not found" in result["message"].lower()


@pytest.mark.queue
@pytest.mark.integration
class TestQueuePosition:
    """Tests for queue position calculation"""

    def setup_method(self):
        """Setup before each test"""
        clear_test_print_queue()

    def teardown_method(self):
        """Cleanup after each test"""
        clear_test_print_queue()

    def test_queue_position_calculation(self):
        """Test queue position is calculated correctly"""
        # Add 5 jobs
        for i in range(1, 6):
            add_job_to_queue(f"TEST-POS-{i:03d}", "00:11:62:12:34:56")
            time.sleep(0.05)

        # Check positions
        pos1 = get_queue_position("00:11:62:12:34:56", "TEST-POS-001")
        pos3 = get_queue_position("00:11:62:12:34:56", "TEST-POS-003")
        pos5 = get_queue_position("00:11:62:12:34:56", "TEST-POS-005")

        assert pos1 == 1
        assert pos3 == 3
        assert pos5 == 5

        frappe.logger().info("✅ Queue position calculation test passed")

    def test_queue_position_after_deletion(self):
        """Test queue positions update after deletion"""
        add_job_to_queue("TEST-POS-A", "00:11:62:12:34:56")
        add_job_to_queue("TEST-POS-B", "00:11:62:12:34:56")
        add_job_to_queue("TEST-POS-C", "00:11:62:12:34:56")

        # Delete middle job
        mark_job_printed("TEST-POS-B")

        # Positions should update
        pos_a = get_queue_position("00:11:62:12:34:56", "TEST-POS-A")
        pos_c = get_queue_position("00:11:62:12:34:56", "TEST-POS-C")

        assert pos_a == 1
        assert pos_c == 2  # Moved up from 3


@pytest.mark.queue
@pytest.mark.integration
class TestQueueStatus:
    """Tests for queue status and monitoring"""

    def setup_method(self):
        """Setup before each test"""
        clear_test_print_queue()

    def teardown_method(self):
        """Cleanup after each test"""
        clear_test_print_queue()

    def test_get_queue_status_all_printers(self):
        """Test get_queue_status for all printers"""
        # Add jobs for 2 printers
        add_job_to_queue("TEST-STATUS-P1", "00:11:62:11:11:11")
        add_job_to_queue("TEST-STATUS-P2", "00:11:62:22:22:22")

        status = get_queue_status()

        assert "total_jobs" in status
        assert status["total_jobs"] >= 2
        assert "jobs" in status
        assert isinstance(status["jobs"], list)

        frappe.logger().info("✅ Queue status all printers test passed")

    def test_get_queue_status_specific_printer(self):
        """Test get_queue_status for specific printer"""
        add_job_to_queue("TEST-STATUS-S1", "00:11:62:12:34:56")
        add_job_to_queue("TEST-STATUS-S2", "00:11:62:12:34:56")

        status = get_queue_status("00:11:62:12:34:56")

        assert "printer_mac" in status
        assert status["printer_mac"] == "00:11:62:12:34:56"
        assert len(status["jobs"]) >= 2


@pytest.mark.queue
@pytest.mark.integration
class TestClearQueue:
    """Tests for clearing queue"""

    def setup_method(self):
        """Setup before each test"""
        clear_test_print_queue()

    def teardown_method(self):
        """Cleanup after each test"""
        clear_test_print_queue()

    def test_clear_queue_all_printers(self):
        """Test clear_queue removes all jobs"""
        add_job_to_queue("TEST-CLEAR-A", "00:11:62:11:11:11")
        add_job_to_queue("TEST-CLEAR-B", "00:11:62:22:22:22")

        result = clear_queue()

        assert result["success"] == True

        # Verify all gone
        status = get_queue_status()
        test_jobs = [j for j in status["jobs"] if j["job_token"].startswith("TEST-CLEAR")]
        assert len(test_jobs) == 0

        frappe.logger().info("✅ Clear queue all test passed")

    def test_clear_queue_specific_printer(self):
        """Test clear_queue for specific printer only"""
        add_job_to_queue("TEST-CLEAR-P1", "00:11:62:11:11:11")
        add_job_to_queue("TEST-CLEAR-P2", "00:11:62:22:22:22")

        # Clear only P1
        result = clear_queue("00:11:62:11:11:11")

        assert result["success"] == True

        # P1 should be empty
        p1_job = get_next_job("00:11:62:11:11:11")
        assert p1_job is None

        # P2 should still have job
        p2_job = get_next_job("00:11:62:22:22:22")
        assert p2_job is not None


@pytest.mark.queue
@pytest.mark.integration
@pytest.mark.slow
class TestQueueConcurrency:
    """Tests for concurrent queue operations"""

    def setup_method(self):
        """Setup before each test"""
        clear_test_print_queue()

    def teardown_method(self):
        """Cleanup after each test"""
        clear_test_print_queue()

    def test_concurrent_job_addition(self):
        """Test adding jobs concurrently (simulated)"""
        # In real scenario, this would use threading
        # For now, just verify sequential adds work correctly

        results = []
        for i in range(10):
            result = add_job_to_queue(
                f"TEST-CONCURRENT-{i:02d}",
                "00:11:62:12:34:56"
            )
            results.append(result)

        # All should succeed
        assert all(r["success"] for r in results)

        # All should have sequential positions
        positions = [r["queue_position"] for r in results]
        assert positions == list(range(1, 11))

        frappe.logger().info("✅ Concurrent job addition test passed")


def run_tests():
    """Helper to run all queue tests"""
    import sys
    exit_code = pytest.main([__file__, "-v", "-m", "queue"])
    return exit_code


if __name__ == "__main__":
    import sys
    sys.exit(run_tests())
