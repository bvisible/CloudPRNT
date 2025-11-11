"""
CloudPRNT MQTT Bridge
=====================

MQTT bridge for CloudPRNT protocol
Enables push notifications to Star printers via MQTT

Optional feature - only used if MQTT broker is configured

Configuration (site_config.json):
{
    "mqtt_broker_host": "localhost",
    "mqtt_broker_port": 1883,
    "mqtt_username": "cloudprnt",
    "mqtt_password": "secure_password"
}

Author: Claude AI
Date: 2025-11-11
"""

import frappe
import json
import threading
import time

try:
    import paho.mqtt.client as mqtt
    MQTT_AVAILABLE = True
except ImportError:
    MQTT_AVAILABLE = False
    frappe.logger().warning("paho-mqtt not installed. MQTT bridge disabled.")


class CloudPRNTMQTTBridge:
    """
    MQTT Bridge for CloudPRNT protocol

    Topics:
    - star/cloudprnt/to-device/{MAC}/print-job - Send print jobs to printer
    - star/cloudprnt/to-device/{MAC}/request-client-status - Request printer status
    - star/cloudprnt/to-server/{MAC}/print-result - Receive print results
    - star/cloudprnt/to-server/{MAC}/client-status - Receive printer status
    """

    def __init__(self):
        if not MQTT_AVAILABLE:
            raise ImportError("paho-mqtt not installed. Install with: pip install paho-mqtt")

        self.client = None
        self.broker_host = frappe.conf.get("mqtt_broker_host", "localhost")
        self.broker_port = frappe.conf.get("mqtt_broker_port", 1883)
        self.username = frappe.conf.get("mqtt_username")
        self.password = frappe.conf.get("mqtt_password")
        self.connected = False
        self.loop_thread = None

        frappe.logger().info(f"CloudPRNT MQTT Bridge initialized: {self.broker_host}:{self.broker_port}")

    def connect(self):
        """Connect to MQTT broker"""
        try:
            # Create client
            client_id = f"cloudprnt-{frappe.local.site}-{int(time.time())}"
            self.client = mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311)

            # Set callbacks
            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect
            self.client.on_message = self._on_message

            # Set credentials if provided
            if self.username and self.password:
                self.client.username_pw_set(self.username, self.password)

            # Connect
            frappe.logger().info(f"Connecting to MQTT broker: {self.broker_host}:{self.broker_port}")
            self.client.connect(self.broker_host, self.broker_port, keepalive=60)

            # Start loop in background thread
            self.loop_thread = threading.Thread(target=self._loop_forever, daemon=True)
            self.loop_thread.start()

            frappe.logger().info("MQTT client loop started")

        except Exception as e:
            frappe.log_error(f"MQTT connection error: {str(e)}", "mqtt_bridge_connect")
            raise

    def _loop_forever(self):
        """Run MQTT loop in background thread"""
        try:
            self.client.loop_forever()
        except Exception as e:
            frappe.log_error(f"MQTT loop error: {str(e)}", "mqtt_loop")

    def _on_connect(self, client, userdata, flags, rc):
        """Callback when connected to broker"""
        if rc == 0:
            self.connected = True
            frappe.logger().info("✅ Connected to MQTT broker")

            # Subscribe to all to-server topics (responses from printers)
            client.subscribe("star/cloudprnt/to-server/+/+", qos=1)
            frappe.logger().info("📡 Subscribed to star/cloudprnt/to-server/+/+")

        else:
            self.connected = False
            error_messages = {
                1: "Connection refused - incorrect protocol version",
                2: "Connection refused - invalid client identifier",
                3: "Connection refused - server unavailable",
                4: "Connection refused - bad username or password",
                5: "Connection refused - not authorized"
            }
            error_msg = error_messages.get(rc, f"Unknown error code: {rc}")
            frappe.log_error(f"MQTT connection failed: {error_msg}", "mqtt_on_connect")

    def _on_disconnect(self, client, userdata, rc):
        """Callback when disconnected from broker"""
        self.connected = False
        if rc != 0:
            frappe.logger().warning(f"⚠️  Unexpected MQTT disconnection (code: {rc})")
        else:
            frappe.logger().info("MQTT disconnected")

    def _on_message(self, client, userdata, msg):
        """Callback when message received from broker"""
        try:
            topic = msg.topic
            payload = msg.payload.decode('utf-8')

            frappe.logger().info(f"📨 MQTT message: {topic}")

            # Parse topic
            parts = topic.split('/')
            if len(parts) < 4:
                frappe.logger().warning(f"Invalid topic format: {topic}")
                return

            direction = parts[2]  # to-server or to-device
            mac_address = parts[3]  # MAC address
            method = parts[4] if len(parts) > 4 else None  # print-result, client-status, etc.

            if direction != "to-server":
                frappe.logger().warning(f"Unexpected direction: {direction}")
                return

            # Parse payload
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                frappe.logger().warning(f"Invalid JSON payload: {payload}")
                return

            # Dispatch based on method
            if method == "print-result":
                self._handle_print_result(mac_address, data)
            elif method == "client-status":
                self._handle_client_status(mac_address, data)
            else:
                frappe.logger().warning(f"Unknown method: {method}")

        except Exception as e:
            frappe.log_error(f"Error handling MQTT message: {str(e)}", "mqtt_on_message")

    def _handle_print_result(self, mac_address, data):
        """
        Handle print-result message from printer

        Payload example:
        {
            "title": "print-result",
            "jobToken": "POS-INV-001",
            "printSucceeded": true,
            "statusCode": "200 OK",
            "printerMAC": "00:11:62:aa:bb:cc"
        }
        """
        try:
            job_token = data.get("jobToken")
            print_succeeded = data.get("printSucceeded", False)
            status_code = data.get("statusCode")

            frappe.logger().info(
                f"Print result: token={job_token}, success={print_succeeded}, status={status_code}"
            )

            # Update printer status
            from cloudprnt.cloudprnt_server import update_printer_status
            mac_normalized = mac_address.replace(".", ":")
            update_printer_status(
                mac_normalized,
                status_code=status_code,
                printing_in_progress=False
            )

            # Log result
            if print_succeeded:
                frappe.logger().info(f"✅ Print succeeded: {job_token}")
            else:
                frappe.logger().warning(f"❌ Print failed: {job_token}")
                frappe.log_error(
                    f"Print failed for job {job_token}: {status_code}",
                    "mqtt_print_failed"
                )

        except Exception as e:
            frappe.log_error(f"Error handling print result: {str(e)}", "handle_print_result")

    def _handle_client_status(self, mac_address, data):
        """
        Handle client-status message from printer

        Payload example:
        {
            "title": "client-status",
            "printerMAC": "00:11:62:aa:bb:cc",
            "statusCode": "200 OK",
            "printingInProgress": false
        }
        """
        try:
            status_code = data.get("statusCode")
            printing_in_progress = data.get("printingInProgress", False)

            frappe.logger().info(f"Client status: mac={mac_address}, status={status_code}")

            # Update printer status
            from cloudprnt.cloudprnt_server import update_printer_status
            mac_normalized = mac_address.replace(".", ":")
            update_printer_status(
                mac_normalized,
                status_code=status_code,
                printing_in_progress=printing_in_progress
            )

        except Exception as e:
            frappe.log_error(f"Error handling client status: {str(e)}", "handle_client_status")

    def send_print_job(self, mac_address, job_token, job_url):
        """
        Send print job to printer via MQTT (Pass URL mode)

        :param mac_address: MAC address with colons (XX:XX:XX:XX:XX:XX)
        :param job_token: Unique job identifier
        :param job_url: URL to fetch job data
        """
        try:
            if not self.connected:
                raise Exception("MQTT bridge not connected")

            # Convert MAC to dots for topic
            mac_dots = mac_address.replace(":", ".")

            # Create topic
            topic = f"star/cloudprnt/to-device/{mac_dots}/print-job"

            # Create payload (Pass URL mode)
            payload = {
                "title": "print-job",
                "jobToken": job_token,
                "printData": job_url,  # Pass URL mode
                "mediaTypes": ["application/vnd.star.line", "text/vnd.star.markup"]
            }

            # Publish
            result = self.client.publish(topic, json.dumps(payload), qos=1)

            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                frappe.logger().info(f"📤 MQTT print job sent: {job_token} to {mac_address}")
            else:
                raise Exception(f"MQTT publish failed with code: {result.rc}")

        except Exception as e:
            frappe.log_error(f"Error sending MQTT print job: {str(e)}", "send_print_job")
            raise

    def request_client_status(self, mac_address):
        """
        Request printer status via MQTT

        :param mac_address: MAC address with colons
        """
        try:
            if not self.connected:
                raise Exception("MQTT bridge not connected")

            # Convert MAC to dots
            mac_dots = mac_address.replace(":", ".")

            # Create topic
            topic = f"star/cloudprnt/to-device/{mac_dots}/request-client-status"

            # Create payload
            payload = {
                "title": "request-client-status"
            }

            # Publish
            result = self.client.publish(topic, json.dumps(payload), qos=1)

            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                frappe.logger().info(f"📤 Status request sent to {mac_address}")
            else:
                raise Exception(f"MQTT publish failed with code: {result.rc}")

        except Exception as e:
            frappe.log_error(f"Error requesting client status: {str(e)}", "request_client_status")
            raise

    def disconnect(self):
        """Disconnect from MQTT broker"""
        if self.client and self.connected:
            self.client.disconnect()
            self.connected = False
            frappe.logger().info("MQTT disconnected")


# ============================================================================
# SINGLETON INSTANCE
# ============================================================================

_mqtt_bridge = None


def get_mqtt_bridge():
    """
    Get singleton MQTT bridge instance

    :return: CloudPRNTMQTTBridge instance
    """
    global _mqtt_bridge

    if not MQTT_AVAILABLE:
        raise ImportError("paho-mqtt not installed")

    if _mqtt_bridge is None:
        _mqtt_bridge = CloudPRNTMQTTBridge()
        _mqtt_bridge.connect()

    return _mqtt_bridge


def init_mqtt_bridge():
    """
    Initialize MQTT bridge on server startup

    Called from hooks.py
    """
    try:
        # Check if MQTT is configured
        if frappe.conf.get("mqtt_broker_host"):
            if not MQTT_AVAILABLE:
                frappe.logger().warning(
                    "MQTT broker configured but paho-mqtt not installed. "
                    "Install with: pip install paho-mqtt"
                )
                return

            # Initialize bridge
            bridge = get_mqtt_bridge()
            frappe.logger().info("✅ CloudPRNT MQTT Bridge initialized successfully")
        else:
            frappe.logger().info("MQTT broker not configured - MQTT bridge disabled")

    except Exception as e:
        frappe.log_error(f"Failed to initialize MQTT bridge: {str(e)}", "init_mqtt_bridge")


# ============================================================================
# WHITELISTED API METHODS
# ============================================================================

@frappe.whitelist()
def test_mqtt_connection():
    """
    Test MQTT connection

    :return: Connection status
    """
    try:
        if not MQTT_AVAILABLE:
            return {
                "success": False,
                "message": "paho-mqtt not installed"
            }

        if not frappe.conf.get("mqtt_broker_host"):
            return {
                "success": False,
                "message": "MQTT broker not configured in site_config.json"
            }

        bridge = get_mqtt_bridge()

        return {
            "success": bridge.connected,
            "broker": f"{bridge.broker_host}:{bridge.broker_port}",
            "connected": bridge.connected
        }

    except Exception as e:
        return {
            "success": False,
            "message": str(e)
        }


@frappe.whitelist()
def send_test_print_mqtt(mac_address, invoice_name):
    """
    Send test print job via MQTT

    :param mac_address: MAC address
    :param invoice_name: Invoice name
    :return: Result
    """
    try:
        bridge = get_mqtt_bridge()

        # Create job URL
        site_url = frappe.utils.get_url()
        job_url = f"{site_url}/api/method/cloudprnt.cloudprnt_server.cloudprnt_job?mac={mac_address.replace(':', '.')}&token={invoice_name}"

        # Send
        bridge.send_print_job(mac_address, invoice_name, job_url)

        return {
            "success": True,
            "message": f"Test print sent via MQTT to {mac_address}"
        }

    except Exception as e:
        frappe.log_error(str(e), "send_test_print_mqtt")
        return {
            "success": False,
            "message": str(e)
        }
