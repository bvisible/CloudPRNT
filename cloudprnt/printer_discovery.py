"""
Printer Discovery System for CloudPRNT
========================================

Tracks printers that poll the server and allows automatic discovery
Uses Frappe cache (Redis/DB) for multi-worker support
"""

import frappe
from datetime import datetime, timedelta
import json

# Cache key prefix
CACHE_KEY_PREFIX = "cloudprnt_discovered_"
CACHE_TTL = 300  # 5 minutes

def _get_cache_key(mac_address):
    """Get cache key for a MAC address"""
    return f"{CACHE_KEY_PREFIX}{mac_address}"

def _extract_mac_from_key(full_key):
    """Extract MAC address from Redis key"""
    # Keys come as: b'_d2d85ec23990accb|cloudprnt_discovered_00:11:62:AA:BB:CC'
    # or as string: '_d2d85ec23990accb|cloudprnt_discovered_00:11:62:AA:BB:CC'
    
    if isinstance(full_key, bytes):
        full_key = full_key.decode('utf-8')
    
    # Remove DB prefix if present
    if '|' in full_key:
        full_key = full_key.split('|', 1)[1]
    
    # Extract MAC from key
    if full_key.startswith(CACHE_KEY_PREFIX):
        mac = full_key[len(CACHE_KEY_PREFIX):]
        # Skip the master list key
        if mac != '_list':
            return mac
    
    return None

def _get_all_discovered_keys():
    """Get all discovered printer MAC addresses"""
    try:
        # Use get_keys to find all matching keys
        if hasattr(frappe.cache(), 'get_keys'):
            keys = frappe.cache().get_keys(f"{CACHE_KEY_PREFIX}*")
            if keys:
                macs = []
                for key in keys:
                    mac = _extract_mac_from_key(key)
                    if mac:
                        macs.append(mac)
                return macs
        
        # Fallback: use master list
        master_key = f"{CACHE_KEY_PREFIX}_list"
        keys_list = frappe.cache().get_value(master_key)
        if keys_list:
            return json.loads(keys_list)
        return []
    except Exception as e:
        frappe.logger().error(f"Error getting discovered keys: {str(e)}")
        return []

def _add_to_master_list(mac_address):
    """Add MAC to master list of discovered printers"""
    master_key = f"{CACHE_KEY_PREFIX}_list"
    try:
        keys_list = frappe.cache().get_value(master_key)
        if keys_list:
            keys = json.loads(keys_list)
        else:
            keys = []
        
        if mac_address not in keys:
            keys.append(mac_address)
            frappe.cache().set_value(master_key, json.dumps(keys), expires_in_sec=CACHE_TTL)
    except Exception as e:
        frappe.logger().error(f"Error adding to master list: {str(e)}")

def track_printer_poll(mac_address, ip_address=None, client_type=None, status_code=None):
    """
    Track a printer that polled the server

    Called from cloudprnt_poll() endpoint

    :param mac_address: MAC address with colons
    :param ip_address: IP address (optional)
    :param client_type: Client type string (optional)
    :param status_code: Status code (optional)
    """
    try:
        now = datetime.now()
        cache_key = _get_cache_key(mac_address)
        
        # Get existing data
        existing_data = frappe.cache().get_value(cache_key)
        
        if existing_data:
            # Update existing
            try:
                printer_data = json.loads(existing_data)
                printer_data["last_seen"] = now.isoformat()
                printer_data["poll_count"] = printer_data.get("poll_count", 0) + 1
                if ip_address:
                    printer_data["ip_address"] = ip_address
                if client_type:
                    printer_data["client_type"] = client_type
                if status_code:
                    printer_data["status_code"] = status_code
            except:
                # Invalid JSON, create new
                existing_data = None
        
        if not existing_data:
            # New printer discovered
            printer_data = {
                "mac_address": mac_address,
                "ip_address": ip_address,
                "client_type": client_type or "Unknown",
                "status_code": status_code or "Unknown",
                "first_seen": now.isoformat(),
                "last_seen": now.isoformat(),
                "poll_count": 1
            }
            frappe.logger().info(f"🔍 New printer discovered: {mac_address} ({client_type})")
            
            # Add to master list
            _add_to_master_list(mac_address)
        
        # Store in cache with TTL
        frappe.cache().set_value(
            cache_key,
            json.dumps(printer_data),
            expires_in_sec=CACHE_TTL
        )
        
    except Exception as e:
        frappe.logger().error(f"Error tracking printer poll: {str(e)}")


def clean_old_discoveries():
    """
    Remove discoveries older than 5 minutes
    (Cache TTL handles this automatically, but we can clean the master list)
    """
    try:
        master_key = f"{CACHE_KEY_PREFIX}_list"
        keys_list = frappe.cache().get_value(master_key)
        
        if not keys_list:
            return
        
        keys = json.loads(keys_list)
        cutoff = datetime.now() - timedelta(minutes=5)
        valid_keys = []
        
        for mac in keys:
            cache_key = _get_cache_key(mac)
            data = frappe.cache().get_value(cache_key)
            if data:
                try:
                    printer_data = json.loads(data)
                    last_seen = datetime.fromisoformat(printer_data["last_seen"])
                    if last_seen >= cutoff:
                        valid_keys.append(mac)
                except:
                    pass
        
        # Update master list
        if len(valid_keys) != len(keys):
            frappe.cache().set_value(master_key, json.dumps(valid_keys), expires_in_sec=CACHE_TTL)
        
    except Exception as e:
        frappe.logger().error(f"Error cleaning discoveries: {str(e)}")


@frappe.whitelist()
def get_discovered_printers():
    """
    Get list of discovered printers that are not yet in CloudPRNT Settings

    :return: List of discovered printers
    """
    try:
        # Clean old discoveries first
        clean_old_discoveries()

        # Get existing printers from settings
        settings = frappe.get_single("CloudPRNT Settings")
        existing_macs = set()
        for printer in settings.printers:
            existing_macs.add(printer.mac_address.upper())

        # Get all discovered printers from cache
        discovered_macs = _get_all_discovered_keys()
        new_printers = []
        total_discovered = 0
        
        for mac in discovered_macs:
            cache_key = _get_cache_key(mac)
            data = frappe.cache().get_value(cache_key)
            
            if not data:
                continue
            
            total_discovered += 1
            
            try:
                printer_data = json.loads(data)
                
                # Skip if already in settings
                if printer_data["mac_address"].upper() in existing_macs:
                    continue
                
                # Calculate time since first seen
                first_seen = datetime.fromisoformat(printer_data["first_seen"])
                time_since = datetime.now() - first_seen
                seconds = int(time_since.total_seconds())

                new_printers.append({
                    "mac_address": printer_data["mac_address"],
                    "ip_address": printer_data.get("ip_address") or "Unknown",
                    "client_type": printer_data.get("client_type", "Unknown"),
                    "status_code": printer_data.get("status_code", "Unknown"),
                    "poll_count": printer_data.get("poll_count", 0),
                    "time_since_first_seen": f"{seconds}s ago"
                })
            except Exception as e:
                frappe.logger().error(f"Error parsing printer data for {mac}: {str(e)}")
                continue

        return {
            "success": True,
            "printers": new_printers,
            "total_discovered": total_discovered,
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
        cache_key = _get_cache_key(mac_address)
        data = frappe.cache().get_value(cache_key)
        
        if not data:
            return {
                "success": False,
                "message": f"Printer {mac_address} not found in discovered printers"
            }

        # Get printer data
        printer_data = json.loads(data)

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

        # Remove from cache
        frappe.cache().delete_value(cache_key)
        
        # Remove from master list
        master_key = f"{CACHE_KEY_PREFIX}_list"
        keys_list = frappe.cache().get_value(master_key)
        if keys_list:
            keys = json.loads(keys_list)
            if mac_address in keys:
                keys.remove(mac_address)
                frappe.cache().set_value(master_key, json.dumps(keys), expires_in_sec=CACHE_TTL)

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
    try:
        # Get all discovered MACs
        discovered_macs = _get_all_discovered_keys()
        count = len(discovered_macs)
        
        # Delete each cache entry
        for mac in discovered_macs:
            cache_key = _get_cache_key(mac)
            frappe.cache().delete_value(cache_key)
        
        # Clear master list
        master_key = f"{CACHE_KEY_PREFIX}_list"
        frappe.cache().delete_value(master_key)
        
        return {
            "success": True,
            "message": f"Cleared {count} discoveries"
        }
    except Exception as e:
        return {
            "success": False,
            "message": str(e)
        }
