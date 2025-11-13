"""
Tests for CloudPRNT Standalone Server (FastAPI)
================================================

Tests the standalone FastAPI server that runs 24/7 in production.
Critical tests for /poll, /job, /delete endpoints.

Run: bench --site sitename run-tests cloudprnt.tests.test_standalone_server
"""

import pytest
import frappe
from fastapi.testclient import TestClient
from cloudprnt.tests.utils import (
    create_test_print_job,
    clear_test_print_queue,
    assert_hex_valid,
    mock_printer_meta
)


# Import the standalone server app
try:
    from cloudprnt.cloudprnt_standalone_server import app
    STANDALONE_AVAILABLE = True
except ImportError:
    STANDALONE_AVAILABLE = False
    app = None


@pytest.mark.skipif(not STANDALONE_AVAILABLE, reason="Standalone server not available")
@pytest.mark.standalone
@pytest.mark.integration
class TestStandaloneServerHealth:
    """Tests for health check endpoint"""

    def setup_method(self):
        """Setup before each test"""
        self.client = TestClient(app)

    def test_health_endpoint_returns_200(self):
        """Test /health endpoint returns 200 OK"""
        response = self.client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "timestamp" in data
        frappe.logger().info("✅ Health endpoint test passed")

    def test_health_endpoint_format(self):
        """Test /health endpoint response format"""
        response = self.client.get("/health")

        data = response.json()
        assert isinstance(data, dict)
        assert "status" in data
        assert "timestamp" in data
        assert data["status"] in ["ok", "error"]


@pytest.mark.skipif(not STANDALONE_AVAILABLE, reason="Standalone server not available")
@pytest.mark.standalone
@pytest.mark.integration
class TestStandaloneServerPoll:
    """Tests for /poll endpoint"""

    def setup_method(self):
        """Setup before each test"""
        self.client = TestClient(app)
        self.test_mac = "00:11:62:12:34:56"
        clear_test_print_queue()

    def teardown_method(self):
        """Cleanup after each test"""
        clear_test_print_queue()

    def test_poll_no_jobs_returns_empty(self):
        """Test /poll with no jobs returns empty response"""
        response = self.client.post("/poll", json={
            "printerMAC": "00.11.62.12.34.56",
            "statusCode": "200 OK"
        })

        assert response.status_code == 200
        data = response.json()
        assert data["jobReady"] == False
        assert "mediaTypes" in data
        assert isinstance(data["mediaTypes"], list)
        frappe.logger().info("✅ Poll no jobs test passed")

    def test_poll_with_job_returns_job_ready(self):
        """Test /poll with pending job returns jobReady=true"""
        # Create a test job
        create_test_print_job("TEST-JOB-001", self.test_mac, "POS-INV-TEST")

        response = self.client.post("/poll", json={
            "printerMAC": "00.11.62.12.34.56",
            "statusCode": "200 OK"
        })

        assert response.status_code == 200
        data = response.json()
        assert data["jobReady"] == True
        assert data["jobToken"] == "TEST-JOB-001"
        assert "mediaTypes" in data
        frappe.logger().info("✅ Poll with job test passed")

    def test_poll_mac_address_normalization(self):
        """Test MAC address normalization (dots → colons)"""
        create_test_print_job("TEST-JOB-002", "00:11:62:12:34:56")

        # Poll with dots
        response = self.client.post("/poll", json={
            "printerMAC": "00.11.62.12.34.56",  # Dots
            "statusCode": "200 OK"
        })

        assert response.status_code == 200
        data = response.json()
        assert data["jobReady"] == True
        frappe.logger().info("✅ MAC address normalization test passed")

    def test_poll_invalid_mac_rejected(self):
        """Test invalid MAC address is handled"""
        response = self.client.post("/poll", json={
            "printerMAC": "invalid-mac",
            "statusCode": "200 OK"
        })

        # Should still return 200 but jobReady=false
        assert response.status_code == 200
        data = response.json()
        assert data["jobReady"] == False


@pytest.mark.skipif(not STANDALONE_AVAILABLE, reason="Standalone server not available")
@pytest.mark.standalone
@pytest.mark.integration
class TestStandaloneServerJob:
    """Tests for /job endpoint"""

    def setup_method(self):
        """Setup before each test"""
        self.client = TestClient(app)
        self.test_mac = "00:11:62:12:34:56"
        clear_test_print_queue()

    def teardown_method(self):
        """Cleanup after each test"""
        clear_test_print_queue()

    def test_job_endpoint_returns_hex(self):
        """Test /job endpoint returns Star Line Mode hex"""
        # Create test job
        create_test_print_job("TEST-JOB-003", self.test_mac, "POS-INV-TEST")

        response = self.client.get("/job", params={
            "mac": "00.11.62.12.34.56",
            "type": "application/vnd.star.line",
            "token": "TEST-JOB-003"
        })

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/vnd.star.line"

        # Verify binary content
        assert len(response.content) > 0
        frappe.logger().info(f"✅ Job hex endpoint test passed ({len(response.content)} bytes)")

    def test_job_endpoint_returns_png_fallback(self):
        """Test /job endpoint returns PNG when requested"""
        create_test_print_job("TEST-JOB-004", self.test_mac, "POS-INV-TEST")

        response = self.client.get("/job", params={
            "mac": "00.11.62.12.34.56",
            "type": "image/png",
            "token": "TEST-JOB-004"
        })

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"

        # Verify PNG signature
        assert response.content[:8] == b'\x89PNG\r\n\x1a\n'
        frappe.logger().info("✅ Job PNG endpoint test passed")

    def test_job_endpoint_invalid_token(self):
        """Test /job endpoint with invalid token"""
        response = self.client.get("/job", params={
            "mac": "00.11.62.12.34.56",
            "type": "application/vnd.star.line",
            "token": "INVALID-TOKEN"
        })

        # Should return 404
        assert response.status_code == 404


@pytest.mark.skipif(not STANDALONE_AVAILABLE, reason="Standalone server not available")
@pytest.mark.standalone
@pytest.mark.integration
class TestStandaloneServerDelete:
    """Tests for /delete endpoint"""

    def setup_method(self):
        """Setup before each test"""
        self.client = TestClient(app)
        self.test_mac = "00:11:62:12:34:56"
        clear_test_print_queue()

    def teardown_method(self):
        """Cleanup after each test"""
        clear_test_print_queue()

    def test_delete_endpoint_removes_job(self):
        """Test /delete endpoint removes job from queue"""
        # Create test job
        create_test_print_job("TEST-JOB-005", self.test_mac)

        # Poll to verify job exists
        poll_response = self.client.post("/poll", json={
            "printerMAC": "00.11.62.12.34.56",
            "statusCode": "200 OK"
        })
        assert poll_response.json()["jobReady"] == True

        # Delete job
        delete_response = self.client.delete("/delete", params={
            "mac": "00.11.62.12.34.56",
            "token": "TEST-JOB-005"
        })

        assert delete_response.status_code == 200

        # Poll again - should be no job
        poll_response2 = self.client.post("/poll", json={
            "printerMAC": "00.11.62.12.34.56",
            "statusCode": "200 OK"
        })
        assert poll_response2.json()["jobReady"] == False
        frappe.logger().info("✅ Delete endpoint test passed")

    def test_delete_endpoint_invalid_token(self):
        """Test /delete with invalid token"""
        response = self.client.delete("/delete", params={
            "mac": "00.11.62.12.34.56",
            "token": "INVALID-TOKEN"
        })

        # Should still return 200 (idempotent)
        assert response.status_code == 200


@pytest.mark.skipif(not STANDALONE_AVAILABLE, reason="Standalone server not available")
@pytest.mark.standalone
@pytest.mark.integration
@pytest.mark.slow
class TestStandaloneServerConcurrency:
    """Tests for concurrent requests"""

    def setup_method(self):
        """Setup before each test"""
        self.client = TestClient(app)
        clear_test_print_queue()

    def teardown_method(self):
        """Cleanup after each test"""
        clear_test_print_queue()

    def test_concurrent_requests_different_printers(self):
        """Test concurrent requests for different printers"""
        # Create jobs for 3 different printers
        create_test_print_job("TEST-JOB-P1", "00:11:62:11:11:11")
        create_test_print_job("TEST-JOB-P2", "00:11:62:22:22:22")
        create_test_print_job("TEST-JOB-P3", "00:11:62:33:33:33")

        # Poll concurrently (in practice, TestClient is synchronous)
        response1 = self.client.post("/poll", json={
            "printerMAC": "00.11.62.11.11.11",
            "statusCode": "200 OK"
        })
        response2 = self.client.post("/poll", json={
            "printerMAC": "00.11.62.22.22.22",
            "statusCode": "200 OK"
        })
        response3 = self.client.post("/poll", json={
            "printerMAC": "00.11.62.33.33.33",
            "statusCode": "200 OK"
        })

        # Each should get their own job
        assert response1.json()["jobReady"] == True
        assert response1.json()["jobToken"] == "TEST-JOB-P1"

        assert response2.json()["jobReady"] == True
        assert response2.json()["jobToken"] == "TEST-JOB-P2"

        assert response3.json()["jobReady"] == True
        assert response3.json()["jobToken"] == "TEST-JOB-P3"

        frappe.logger().info("✅ Concurrent requests test passed")


def run_tests():
    """Helper to run all standalone server tests"""
    import sys
    exit_code = pytest.main([__file__, "-v", "-m", "standalone"])
    return exit_code


if __name__ == "__main__":
    sys.exit(run_tests())
