import frappe
import json
import os
import re
from frappe import _ as translate
from frappe.utils import get_bench_path
from cloudprnt.print_job import StarCloudPRNTStarLineModeJob, neolog
from cloudprnt.pos_invoice_markup import get_pos_invoice_markup
from datetime import datetime

@frappe.whitelist()
def print_pos_invoice(invoice_name, printer=None, use_mqtt=False):
    """
    Print a POS Invoice using CloudPRNT
    Version 2.0 - Pure Python implementation (no PHP)

    :param invoice_name: Name of the POS Invoice
    :param printer: MAC address of printer or CloudPRNT Printer label
    :param use_mqtt: Force MQTT mode (optional)
    :return: Success message
    """
    try:
        if not invoice_name:
            return {"success": False, "message": "Aucune facture spécifiée"}

        if not frappe.db.exists("POS Invoice", invoice_name):
            return {"success": False, "message": f"Facture POS {invoice_name} non trouvée"}

        # Get MAC address
        mac_address = None

        if not printer:
            # Get default printer
            printer = frappe.db.get_single_value("CloudPRNT Settings", "default_printer")
            if not printer:
                return {"success": False, "message": "Aucune imprimante par défaut configurée"}

        # Resolve MAC address
        if ":" in printer or "." in printer:
            # MAC address provided directly
            mac_address = printer.replace(".", ":")
        else:
            # Printer label provided - find in settings
            settings = frappe.get_single("CloudPRNT Settings")
            printer_row = None
            for p in settings.printers:
                if p.label == printer:
                    printer_row = p
                    break

            if not printer_row:
                return {"success": False, "message": f"Imprimante {printer} non trouvée"}

            mac_address = printer_row.mac_address

            # Check if printer has MQTT enabled
            if hasattr(printer_row, 'use_mqtt') and printer_row.use_mqtt:
                use_mqtt = True

        # Determine print method
        if use_mqtt and frappe.conf.get("mqtt_broker_host"):
            # MQTT Mode
            try:
                from cloudprnt.mqtt_bridge import get_mqtt_bridge
                bridge = get_mqtt_bridge()

                # Create job URL
                site_url = frappe.utils.get_url()
                job_url = f"{site_url}/api/method/cloudprnt.cloudprnt_server.cloudprnt_job?mac={mac_address.replace(':', '.')}&token={invoice_name}"

                # Send via MQTT
                bridge.send_print_job(mac_address, invoice_name, job_url)

                frappe.logger().info(f"Print job sent via MQTT: {invoice_name} to {mac_address}")

                return {
                    "success": True,
                    "message": f"Impression de la facture {invoice_name} envoyée via MQTT",
                    "method": "mqtt",
                    "printer": printer
                }
            except Exception as mqtt_error:
                # Fallback to HTTP if MQTT fails
                frappe.log_error(f"MQTT failed, falling back to HTTP: {str(mqtt_error)}", "print_pos_invoice")
                use_mqtt = False

        # HTTP Mode (queue)
        from cloudprnt.cloudprnt_server import add_print_job
        result = add_print_job(invoice_name, mac_address)

        if result.get("success"):
            frappe.logger().info(f"Print job added to queue: {invoice_name} for {mac_address}")

            return {
                "success": True,
                "message": f"Impression de la facture {invoice_name} ajoutée à la queue",
                "method": "http",
                "printer": printer,
                "queue_position": result.get("queue_position", 1)
            }
        else:
            return result

    except Exception as e:
        frappe.log_error(message=str(e), title="Error in print_pos_invoice")
        return {"success": False, "message": str(e)}