import frappe
import json
import os
import re
from frappe import _ as translate
from frappe.utils import get_bench_path
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
            # Get default printer from database (bypassing controller)
            default_printer = frappe.db.sql("""
                SELECT default_printer
                FROM `tabSingles`
                WHERE doctype = 'CloudPRNT Settings'
                AND field = 'default_printer'
            """, as_dict=True)

            if default_printer and default_printer[0].get('default_printer'):
                printer = default_printer[0]['default_printer']
            else:
                return {"success": False, "message": "Aucune imprimante par défaut configurée"}

        # Resolve MAC address
        if ":" in printer or "." in printer:
            # MAC address provided directly
            mac_address = printer.replace(".", ":")
        else:
            # Printer label or name provided - find in database (bypassing controller)
            printer_row = frappe.db.sql("""
                SELECT name, label, mac_address, use_mqtt
                FROM `tabCloudPRNT Printers`
                WHERE parent = 'CloudPRNT Settings'
                AND (label = %s OR name = %s)
                LIMIT 1
            """, (printer, printer), as_dict=True)

            if not printer_row:
                return {"success": False, "message": f"Imprimante {printer} non trouvée"}

            printer_row = printer_row[0]
            mac_address = printer_row.mac_address

            # Check if printer has MQTT enabled
            if printer_row.get('use_mqtt'):
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

        # HTTP Mode (database queue)
        from cloudprnt.print_queue_manager import add_job_to_queue

        # Pre-generate Star Markup for the invoice
        try:
            markup_text = get_pos_invoice_markup(invoice_name)
            job_data = markup_text  # Store markup directly, will be converted by standalone server
        except Exception as e:
            frappe.log_error(f"Error generating markup: {str(e)}", "print_pos_invoice")
            job_data = None

        result = add_job_to_queue(
            job_token=invoice_name,
            printer_mac=mac_address,
            invoice_name=invoice_name,
            job_data=job_data,  # Pre-generated binary data
            media_types=["application/vnd.star.starprnt", "application/vnd.star.line", "text/vnd.star.markup"]
        )

        if result.get("success"):
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


@frappe.whitelist()
def print_image_to_cloudprnt(image_path, printer_mac, printer_width=3, dither=True, scale_to_fit=True, drawer_end=False, buzzer_end=0):
    """
    Print an image (PNG/JPEG/BMP/GIF) to a CloudPRNT printer

    Example usage for printing company logo on receipts:

    >>> from cloudprnt.api import print_image_to_cloudprnt
    >>> print_image_to_cloudprnt(
    ...     '/path/to/logo.png',
    ...     '00:11:62:12:34:56',
    ...     printer_width=3,
    ...     dither=True,
    ...     scale_to_fit=True
    ... )

    :param image_path: Path to image file (PNG, JPEG, BMP, GIF)
    :param printer_mac: MAC address of printer (with colons)
    :param printer_width: Printer width - 2 (58mm), 3 (80mm), or 4 (112mm) - default 3
    :param dither: Apply dithering for better quality (default True)
    :param scale_to_fit: Scale image to fit printer width (default True)
    :param drawer_end: Open cash drawer after printing (default False)
    :param buzzer_end: Buzzer beeps after printing 0-9 (default 0)
    :return: Success response with job token
    """
    from cloudprnt.cputil_wrapper import convert_png_to_starprnt, is_cputil_available
    from cloudprnt.print_queue_manager import add_job_to_queue
    import uuid

    try:
        # Check CPUtil availability
        if not is_cputil_available():
            return {
                "success": False,
                "message": translate("CPUtil n'est pas disponible. Impossible de convertir l'image.")
            }

        # Normalize MAC address
        printer_mac = printer_mac.replace(".", ":")

        # Build conversion options
        options = {
            'printer_width': int(printer_width),
            'dither': bool(dither),
            'scale_to_fit': bool(scale_to_fit),
            'partial_cut': True
        }

        if drawer_end:
            options['drawer'] = 'end'

        if buzzer_end and int(buzzer_end) > 0:
            options['buzzer_end'] = int(buzzer_end)

        # Convert image to StarPRNT binary
        binary_data = convert_png_to_starprnt(image_path, options)

        # Convert to hex for database storage
        hex_data = binary_data.hex().upper()

        # Generate job token
        job_token = f"IMG-{uuid.uuid4().hex[:8].upper()}"

        # Add to print queue
        result = add_job_to_queue(
            job_token=job_token,
            printer_mac=printer_mac,
            job_data=hex_data,
            media_types=["application/vnd.star.starprnt"]
        )

        if result.get("success"):
            return {
                "success": True,
                "job_token": job_token,
                "message": translate("Image ajoutée à la queue d'impression ({0} bytes)").format(len(binary_data)),
                "bytes": len(binary_data)
            }
        else:
            return result

    except FileNotFoundError as e:
        frappe.log_error(str(e), "print_image_to_cloudprnt - File Not Found")
        return {
            "success": False,
            "message": translate("Fichier image introuvable: {0}").format(image_path)
        }
    except Exception as e:
        frappe.log_error(str(e), "print_image_to_cloudprnt")
        return {
            "success": False,
            "message": translate("Erreur lors de l'impression de l'image: {0}").format(str(e))
        }