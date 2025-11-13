"""
Printer Discovery System for CloudPRNT
========================================

Tracks printers that poll the server and allows automatic discovery
"""

import frappe
from datetime import datetime, timedelta

# Store discovered printers temporarily (in-memory)
DISCOVERED_PRINTERS = {}
# Structure: {
#     "00:11:62:12:34:56": {
#         "mac_address": "00:11:62:12:34:56",
#         "ip_address": "192.168.1.100",
#         "client_type": "Star mC-Print3",
#         "status_code": "200 OK",
#         "first_seen": datetime,
#         "last_seen": datetime,
#         "poll_count": 5
#     }
# }


def track_printer_poll(mac_address, ip_address=None, client_type=None, status_code=None):
    """
    Track a printer that polled the server

    Called from cloudprnt_poll() endpoint

    :param mac_address: MAC address with colons
    :param ip_address: IP address (optional)
    :param client_type: Client type string (optional)
    :param status_code: Status code (optional)
    """
    now = datetime.now()

    if mac_address not in DISCOVERED_PRINTERS:
        # New printer discovered
        DISCOVERED_PRINTERS[mac_address] = {
            "mac_address": mac_address,
            "ip_address": ip_address,
            "client_type": client_type or "Unknown",
            "status_code": status_code or "Unknown",
            "first_seen": now,
            "last_seen": now,
            "poll_count": 1
        }
        frappe.logger().info(f"🔍 New printer discovered: {mac_address} ({client_type})")
    else:
        # Update existing
        printer = DISCOVERED_PRINTERS[mac_address]
        printer["last_seen"] = now
        printer["poll_count"] += 1
        if ip_address:
            printer["ip_address"] = ip_address
        if client_type:
            printer["client_type"] = client_type
        if status_code:
            printer["status_code"] = status_code


def clean_old_discoveries():
    """
    Remove discoveries older than 5 minutes
    """
    cutoff = datetime.now() - timedelta(minutes=5)
    to_remove = []

    for mac, data in DISCOVERED_PRINTERS.items():
        if data["last_seen"] < cutoff:
            to_remove.append(mac)

    for mac in to_remove:
        del DISCOVERED_PRINTERS[mac]

    if len(to_remove) > 0:
        frappe.logger().info(f"Cleaned {len(to_remove)} old discoveries")


@frappe.whitelist()
def get_discovered_printers():
    """
    Get list of discovered printers that are not yet in CloudPRNT Settings

    :return: List of discovered printers
    """
    try:
        # Clean old discoveries first
        clean_old_discoveries()

        # Get existing printers
        settings = frappe.get_single("CloudPRNT Settings")
        existing_macs = set()
        for printer in settings.printers:
            existing_macs.add(printer.mac_address.upper())

        # Filter out printers that already exist
        new_printers = []
        for mac, data in DISCOVERED_PRINTERS.items():
            if mac.upper() not in existing_macs:
                # Calculate time since first seen
                time_since = datetime.now() - data["first_seen"]
                seconds = int(time_since.total_seconds())

                new_printers.append({
                    "mac_address": data["mac_address"],
                    "ip_address": data["ip_address"] or "Unknown",
                    "client_type": data["client_type"],
                    "status_code": data["status_code"],
                    "poll_count": data["poll_count"],
                    "time_since_first_seen": f"{seconds}s ago"
                })

        return {
            "success": True,
            "printers": new_printers,
            "total_discovered": len(DISCOVERED_PRINTERS),
            "new_printers": len(new_printers)
        }

    except Exception as e:
        frappe.log_error(f"Error getting discovered printers: {str(e)}", "get_discovered_printers")
        return {
            "success": False,
            "message": str(e)
        }


@frappe.whitelist()
def add_discovered_printer(mac_address, label=None):
    """
    Add a discovered printer to CloudPRNT Settings

    :param mac_address: MAC address to add
    :param label: Optional label (will auto-generate if not provided)
    :return: Success/error dict
    """
    try:
        # Check if printer was discovered
        if mac_address not in DISCOVERED_PRINTERS:
            return {
                "success": False,
                "message": f"Printer {mac_address} not found in discovered printers"
            }

        # Get printer data
        printer_data = DISCOVERED_PRINTERS[mac_address]

        # Generate label if not provided
        if not label:
            client_type = printer_data.get("client_type", "Printer")
            # Extract model name (e.g., "Star mC-Print3" -> "mC-Print3")
            if "Star" in client_type:
                label = client_type.replace("Star ", "")
            else:
                label = client_type

            # Add MAC last 4 chars to make unique
            label = f"{label} ({mac_address[-5:]})"

        # Get settings
        settings = frappe.get_single("CloudPRNT Settings")

        # Check if already exists
        for printer in settings.printers:
            if printer.mac_address == mac_address:
                return {
                    "success": False,
                    "message": f"Printer {mac_address} already exists"
                }

        # Add new printer row
        settings.append("printers", {
            "mac_address": mac_address,
            "label": label,
            "ip_address": printer_data.get("ip_address"),
            "online": 1,
            "status_code": printer_data.get("status_code", "200 OK")
        })

        settings.save(ignore_permissions=True)
        frappe.db.commit()

        # Remove from discovered list
        del DISCOVERED_PRINTERS[mac_address]

        frappe.logger().info(f"✅ Added printer: {label} ({mac_address})")

        return {
            "success": True,
            "message": f"Printer {label} added successfully",
            "label": label
        }

    except Exception as e:
        frappe.log_error(f"Error adding discovered printer: {str(e)}", "add_discovered_printer")
        return {
            "success": False,
            "message": str(e)
        }


@frappe.whitelist()
def clear_discoveries():
    """
    Clear all discovered printers (for testing)

    :return: Success message
    """
    global DISCOVERED_PRINTERS
    count = len(DISCOVERED_PRINTERS)
    DISCOVERED_PRINTERS.clear()
    return {
        "success": True,
        "message": f"Cleared {count} discoveries"
    }
