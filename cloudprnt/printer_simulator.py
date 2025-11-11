"""
CloudPRNT Printer Simulator
============================

Simulates a Star mC-Print3 printer for testing the CloudPRNT server

Usage:
    bench --site your-site execute cloudprnt.printer_simulator.run_simulator

Author: Claude AI
Date: 2025-11-11
"""

import requests
import time
import json
import os
from datetime import datetime


class CloudPRNTPrinterSimulator:
    """
    Simulates a Star mC-Print3 printer polling a CloudPRNT server
    """

    def __init__(self, mac_address, server_url, poll_interval=5, output_dir="./output"):
        """
        Initialize printer simulator

        :param mac_address: MAC address in format XX:XX:XX:XX:XX:XX
        :param server_url: Base URL of CloudPRNT server
        :param poll_interval: Seconds between polls (default: 5)
        :param output_dir: Directory to save received jobs
        """
        self.mac_address = mac_address
        self.mac_display = mac_address.replace(":", ".")  # CloudPRNT uses dots
        self.server_url = server_url.rstrip("/")
        self.poll_interval = poll_interval
        self.output_dir = output_dir
        self.running = False
        self.job_count = 0

        # Create output directory
        os.makedirs(self.output_dir, exist_ok=True)

        print("=" * 80)
        print("🖨️  CloudPRNT Printer Simulator")
        print("=" * 80)
        print(f"MAC Address:    {self.mac_address} (displayed as {self.mac_display})")
        print(f"Server URL:     {self.server_url}")
        print(f"Poll Interval:  {self.poll_interval}s")
        print(f"Output Dir:     {self.output_dir}")
        print("=" * 80)

    def start(self):
        """Start the simulator (polling loop)"""
        self.running = True
        print(f"\n✅ Simulator started at {datetime.now().strftime('%H:%M:%S')}")
        print("📡 Polling server...\n")

        try:
            while self.running:
                self.poll_server()
                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            print("\n\n⚠️  Simulator stopped by user")
            self.running = False

    def stop(self):
        """Stop the simulator"""
        self.running = False

    def poll_server(self):
        """
        Poll the CloudPRNT server (POST request)
        """
        try:
            # Prepare poll request
            poll_url = f"{self.server_url}/api/method/cloudprnt.cloudprnt_server.cloudprnt_poll"
            poll_data = {
                "printerMAC": self.mac_display,
                "statusCode": "200 OK",
                "clientType": "Star mC-Print3",
                "clientVersion": "3.0",
                "printingInProgress": False
            }

            # Send poll request
            response = requests.post(poll_url, json=poll_data, timeout=10)

            if response.status_code != 200:
                print(f"❌ Poll failed: HTTP {response.status_code}")
                return

            # Parse response
            data = response.json()

            if data.get("jobReady"):
                job_token = data.get("jobToken")
                media_types = data.get("mediaTypes", [])

                print(f"📥 Job available: {job_token}")
                print(f"   Media types: {media_types}")

                # Fetch job
                self.fetch_job(job_token, media_types)
            else:
                # No job
                timestamp = datetime.now().strftime("%H:%M:%S")
                print(f"⏱️  [{timestamp}] Poll: No jobs waiting")

        except requests.exceptions.Timeout:
            print("⚠️  Poll timeout")
        except requests.exceptions.ConnectionError:
            print("❌ Connection error - is the server running?")
        except Exception as e:
            print(f"❌ Poll error: {str(e)}")

    def fetch_job(self, job_token, media_types):
        """
        Fetch and 'print' a job

        :param job_token: Job token from server
        :param media_types: List of supported media types
        """
        try:
            # Choose preferred media type (Star Line Mode)
            media_type = "application/vnd.star.line"
            if media_type not in media_types and len(media_types) > 0:
                media_type = media_types[0]

            # Fetch job
            job_url = f"{self.server_url}/api/method/cloudprnt.cloudprnt_server.cloudprnt_job"
            params = {
                "mac": self.mac_display,
                "type": media_type,
                "token": job_token
            }

            print(f"   📡 Fetching job: {media_type}")

            response = requests.get(job_url, params=params, timeout=10)

            if response.status_code != 200:
                print(f"   ❌ Fetch failed: HTTP {response.status_code}")
                return

            # Save job to file
            self.job_count += 1
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            if media_type == "application/vnd.star.line":
                # Binary data (hex)
                filename = f"{timestamp}_{self.job_count}_{job_token}.slt"
                filepath = os.path.join(self.output_dir, filename)

                with open(filepath, 'wb') as f:
                    f.write(response.content)

                # Also save as hex for inspection
                hex_filename = f"{timestamp}_{self.job_count}_{job_token}.hex"
                hex_filepath = os.path.join(self.output_dir, hex_filename)

                with open(hex_filepath, 'w') as f:
                    f.write(response.content.hex().upper())

                print(f"   ✅ Job saved: {filename} ({len(response.content)} bytes)")
                print(f"   📄 Hex saved: {hex_filename}")

            else:
                # Text data (markup)
                filename = f"{timestamp}_{self.job_count}_{job_token}.txt"
                filepath = os.path.join(self.output_dir, filename)

                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(response.text)

                print(f"   ✅ Job saved: {filename}")

            # Simulate printing delay
            print(f"   🖨️  Printing...")
            time.sleep(2)

            # Send delete confirmation
            self.confirm_print(job_token)

        except Exception as e:
            print(f"   ❌ Fetch error: {str(e)}")

    def confirm_print(self, job_token):
        """
        Send print confirmation to server (DELETE request)

        :param job_token: Job token
        """
        try:
            delete_url = f"{self.server_url}/api/method/cloudprnt.cloudprnt_server.cloudprnt_delete"
            delete_data = {
                "printerMAC": self.mac_display,
                "statusCode": "200 OK",
                "jobToken": job_token
            }

            response = requests.delete(delete_url, json=delete_data, timeout=10)

            if response.status_code == 200:
                print(f"   ✅ Print confirmed\n")
            else:
                print(f"   ⚠️  Confirm failed: HTTP {response.status_code}\n")

        except Exception as e:
            print(f"   ❌ Confirm error: {str(e)}\n")


# ============================================================================
# HELPER FUNCTIONS FOR FRAPPE
# ============================================================================

def run_simulator(mac_address=None, server_url=None, poll_interval=5):
    """
    Run the simulator with default values from CloudPRNT Settings

    :param mac_address: MAC address (uses default printer if not provided)
    :param server_url: Server URL (uses frappe.utils.get_url() if not provided)
    :param poll_interval: Seconds between polls
    """
    import frappe

    try:
        # Get server URL
        if not server_url:
            server_url = frappe.utils.get_url()

        # Get MAC address
        if not mac_address:
            # Get default printer
            default_printer = frappe.db.get_single_value("CloudPRNT Settings", "default_printer")
            if not default_printer:
                print("❌ No default printer configured")
                return

            # Get MAC from printer
            settings = frappe.get_single("CloudPRNT Settings")
            printer_row = None
            for printer in settings.printers:
                if printer.label == default_printer:
                    printer_row = printer
                    break

            if not printer_row:
                print(f"❌ Printer {default_printer} not found")
                return

            mac_address = printer_row.mac_address

        # Create and start simulator
        simulator = CloudPRNTPrinterSimulator(
            mac_address=mac_address,
            server_url=server_url,
            poll_interval=poll_interval,
            output_dir="./cloudprnt_output"
        )

        simulator.start()

    except Exception as e:
        print(f"❌ Simulator error: {str(e)}")
        import traceback
        traceback.print_exc()


def run_simulator_cli():
    """
    CLI wrapper for running the simulator

    Usage:
        bench --site your-site execute cloudprnt.printer_simulator.run_simulator_cli
    """
    import sys

    # Parse command line args
    mac_address = None
    server_url = None
    poll_interval = 5

    if len(sys.argv) > 1:
        mac_address = sys.argv[1]
    if len(sys.argv) > 2:
        server_url = sys.argv[2]
    if len(sys.argv) > 3:
        poll_interval = int(sys.argv[3])

    run_simulator(mac_address, server_url, poll_interval)


# ============================================================================
# STANDALONE MODE (for testing without Frappe)
# ============================================================================

if __name__ == "__main__":
    """
    Run simulator in standalone mode

    Usage:
        python printer_simulator.py [mac_address] [server_url] [poll_interval]

    Example:
        python printer_simulator.py 00:11:62:12:34:56 http://localhost:8000 5
    """
    import sys

    # Default values
    mac_address = "00:11:62:12:34:56"
    server_url = "http://localhost:8000"
    poll_interval = 5

    # Parse command line args
    if len(sys.argv) > 1:
        mac_address = sys.argv[1]
    if len(sys.argv) > 2:
        server_url = sys.argv[2]
    if len(sys.argv) > 3:
        poll_interval = int(sys.argv[3])

    # Create and start simulator
    simulator = CloudPRNTPrinterSimulator(
        mac_address=mac_address,
        server_url=server_url,
        poll_interval=poll_interval,
        output_dir="./cloudprnt_output"
    )

    simulator.start()
