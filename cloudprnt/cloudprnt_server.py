"""
CloudPRNT Server - Pure Python Implementation
==============================================

Serveur CloudPRNT 100% Python pour Frappe Framework
Compatible avec les imprimantes Star Micronics (mC-Print3)
Supporte le protocole CloudPRNT HTTP (polling)

Author: Claude AI
Date: 2025-11-11
Version: 2.0 (Migration PHP -> Python)
"""

import frappe
import json
import re
from datetime import datetime
from cloudprnt.print_job import StarCloudPRNTStarLineModeJob
from cloudprnt.pos_invoice_markup import get_pos_invoice_markup

# ============================================================================
# PRINT QUEUE - In-memory storage
# ============================================================================
# Structure: {
#     "00:11:62:12:34:56": [
#         {
#             "token": "POS-INV-001",
#             "invoice": "POS-INV-001",
#             "printer_mac": "00:11:62:12:34:56",
#             "timestamp": "2025-01-01 12:00:00",
#             "status": "pending",
#             "media_types": ["application/vnd.star.line", "text/vnd.star.markup"]
#         }
#     ]
# }
PRINT_QUEUE = {}

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def normalize_mac_address(mac_address):
    """
    Normalize MAC address format
    CloudPRNT uses dots (00.11.62.12.34.56)
    Frappe DB uses colons (00:11:62:12:34:56)

    :param mac_address: MAC address in any format
    :return: MAC address with colons (XX:XX:XX:XX:XX:XX)
    """
    if not mac_address:
        return None

    # Replace dots with colons
    mac_normalized = mac_address.replace(".", ":")

    # Validate format
    if len(mac_normalized.replace(":", "")) != 12:
        return None

    return mac_normalized.upper()


def mac_to_dots(mac_address):
    """
    Convert MAC address to dot format for CloudPRNT
    :param mac_address: MAC with colons (XX:XX:XX:XX:XX:XX)
    :return: MAC with dots (XX.XX.XX.XX.XX.XX)
    """
    return mac_address.replace(":", ".")


def update_printer_status(mac_address, status_code=None, printing_in_progress=None, **kwargs):
    """
    Update printer status in CloudPRNT Printers DocType

    :param mac_address: MAC address (with colons)
    :param status_code: Status code from printer
    :param printing_in_progress: Boolean
    :param kwargs: Additional fields to update
    """
    try:
        # Find printer in CloudPRNT Settings
        settings = frappe.get_single("CloudPRNT Settings")

        # Find printer row
        printer_row = None
        for printer in settings.printers:
            if normalize_mac_address(printer.mac_address) == normalize_mac_address(mac_address):
                printer_row = printer
                break

        if not printer_row:
            frappe.log_error(
                f"Printer with MAC {mac_address} not found in CloudPRNT Settings",
                "update_printer_status"
            )
            return

        # Update fields
        printer_row.online = 1
        printer_row.last_activity = frappe.utils.now_datetime().timestamp()

        if status_code is not None:
            printer_row.status_code = status_code

        if printing_in_progress is not None:
            printer_row.printing_in_progress = 1 if printing_in_progress else 0

        # Update additional fields from kwargs
        for key, value in kwargs.items():
            if hasattr(printer_row, key):
                setattr(printer_row, key, value)

        # Save
        settings.save(ignore_permissions=True)
        frappe.db.commit()

    except Exception as e:
        frappe.log_error(f"Error updating printer status: {str(e)}", "update_printer_status")


def generate_star_line_job(job_data):
    """
    Generate Star Line Mode hex from markup

    :param job_data: Job dict with 'invoice' or 'test_markup' and 'printer_mac'
    :return: Hex string (uppercase)
    """
    try:
        invoice_name = job_data.get("invoice")
        printer_mac = job_data.get("printer_mac")

        # Get markup - check if it's a test job first
        if "test_markup" in job_data and job_data["test_markup"]:
            markup_text = job_data["test_markup"]
        else:
            markup_text = get_pos_invoice_markup(invoice_name)

        # Create job
        printer_meta = {'printerMAC': mac_to_dots(printer_mac)}
        job = StarCloudPRNTStarLineModeJob(printer_meta)

        # Parse markup and build job
        lines = markup_text.split('\n')
        current_align = "left"

        def clean_all_tags(text):
            """Remove all Star Markup tags"""
            text = re.sub(r'\[([^\]]+)\]', '', text)
            return text

        for line in lines:
            original_line = line

            # Alignment tags
            if "[align: centre]" in original_line or "[align: center]" in original_line:
                job.set_text_center_align()
                current_align = "center"
            elif "[align: left]" in original_line:
                job.set_text_left_align()
                current_align = "left"
            elif "[align: right]" in original_line:
                job.set_text_right_align()
                current_align = "right"
            elif "[align]" in original_line:
                job.set_text_left_align()
                current_align = "left"

            # Bold/emphasized tags
            if "[magnify:" in original_line or "[bold: on]" in original_line:
                job.set_text_emphasized()
            elif "[magnify]" in original_line or "[bold: off]" in original_line:
                job.cancel_text_emphasized()

            # Feed tags
            if "[feed]" in original_line:
                job.add_new_line(1)
            elif "[feed: length " in original_line:
                match = re.search(r'\[feed: length (\d+(\.\d+)?)mm\]', original_line)
                if match:
                    try:
                        length = int(float(match.group(1)))
                        job.add_new_line(length)
                    except ValueError:
                        pass

            # Cut tags
            if "[cut" in original_line:
                job.cut()
                continue

            # Column tags
            if "[column: left" in original_line:
                match = re.search(r'\[column: left([^;]*); right([^\]]*)\]', original_line)
                if match:
                    left_text = match.group(1).strip()
                    if left_text.startswith(":"):
                        left_text = left_text[1:].strip()

                    right_text = match.group(2).strip()
                    if right_text.startswith(":"):
                        right_text = right_text[1:].strip()

                    job.add_aligned_text(left_text, right_text)
                    continue

            # Clean line and add text
            clean_line = clean_all_tags(original_line)

            if clean_line:
                if clean_line.endswith("\\"):
                    job.add_text(clean_line[:-1])
                else:
                    job.add_text_line(clean_line)

        # Return hex string (uppercase)
        return job.print_job_builder.upper()

    except Exception as e:
        frappe.log_error(f"Error generating Star Line job: {str(e)}", "generate_star_line_job")
        raise


def create_print_log(invoice_name):
    """
    Create a CloudPRNT Log entry

    :param invoice_name: POS Invoice name
    """
    try:
        log_entry = frappe.get_doc({
            "doctype": "CloudPRNT Logs",
            "doctype_link": "POS Invoice",
            "document_link": invoice_name,
            "datetime": frappe.utils.now_datetime()
        })
        log_entry.insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception as e:
        frappe.log_error(f"Error creating print log: {str(e)}", "create_print_log")


# ============================================================================
# CLOUDPRNT HTTP ENDPOINTS
# ============================================================================

@frappe.whitelist(allow_guest=True, methods=['POST'])
def cloudprnt_poll():
    """
    CloudPRNT Poll Endpoint (POST)

    Printer polls the server to check for jobs and send status updates

    Expected JSON body:
    {
        "printerMAC": "00.11.62.12.34.56",
        "statusCode": "200 OK",
        "clientType": "Star mC-Print3",
        "clientVersion": "3.0",
        "printingInProgress": false
    }

    Response JSON:
    {
        "jobReady": true/false,
        "mediaTypes": ["application/vnd.star.line", "text/vnd.star.markup"],
        "jobToken": "unique-job-id"  // if jobReady=true
    }
    """
    try:
        # Parse JSON body
        data = frappe.request.get_json()

        if not data:
            frappe.log_error("No JSON data in poll request", "cloudprnt_poll")
            return {"jobReady": False}

        # Extract printer info
        printer_mac_dots = data.get("printerMAC", "")
        status_code = data.get("statusCode", "")
        printing_in_progress = data.get("printingInProgress", False)
        client_type = data.get("clientType", "")

        # Normalize MAC address
        printer_mac = normalize_mac_address(printer_mac_dots)

        if not printer_mac:
            frappe.log_error(f"Invalid MAC address: {printer_mac_dots}", "cloudprnt_poll")
            return {"jobReady": False}

        # Track for discovery (helps auto-detect new printers)
        try:
            from cloudprnt.printer_discovery import track_printer_poll
            track_printer_poll(
                printer_mac,
                ip_address=frappe.request.remote_addr,
                client_type=client_type,
                status_code=status_code
            )
        except Exception as e:
            # Don't fail if discovery tracking fails
            frappe.logger().debug(f"Discovery tracking failed: {str(e)}")

        # Update printer status
        update_printer_status(
            printer_mac,
            status_code=status_code,
            printing_in_progress=printing_in_progress
        )

        # Check for jobs in queue
        if printer_mac in PRINT_QUEUE and len(PRINT_QUEUE[printer_mac]) > 0:
            # Get first job
            job = PRINT_QUEUE[printer_mac][0]

            frappe.logger().info(f"Job ready for printer {printer_mac}: {job['token']}")

            return {
                "jobReady": True,
                "mediaTypes": job.get("media_types", ["application/vnd.star.line", "text/vnd.star.markup"]),
                "jobToken": job["token"]
            }
        else:
            # No jobs
            return {
                "jobReady": False,
                "mediaTypes": ["application/vnd.star.line", "text/vnd.star.markup"]
            }

    except Exception as e:
        frappe.log_error(f"Error in cloudprnt_poll: {str(e)}", "cloudprnt_poll")
        return {"jobReady": False}


@frappe.whitelist(allow_guest=True, methods=['GET'])
def cloudprnt_job():
    """
    CloudPRNT Job Endpoint (GET)

    Printer fetches the job data

    Query parameters:
    - mac: Printer MAC address (with dots or colons)
    - type: Media type (application/vnd.star.line or text/vnd.star.markup)
    - token: Job token

    Response:
    - Binary data (Star Line Mode hex) for application/vnd.star.line
    - Text data (Star Markup) for text/vnd.star.markup
    """
    try:
        # Get query parameters
        printer_mac_param = frappe.request.args.get("mac", "")
        media_type = frappe.request.args.get("type", "application/vnd.star.line")
        job_token = frappe.request.args.get("token", "")

        # Normalize MAC
        printer_mac = normalize_mac_address(printer_mac_param)

        if not printer_mac or not job_token:
            frappe.log_error(
                f"Missing parameters: mac={printer_mac_param}, token={job_token}",
                "cloudprnt_job"
            )
            frappe.response['http_status_code'] = 400
            return "Missing parameters"

        # Find job in queue
        job = None
        if printer_mac in PRINT_QUEUE:
            for q_job in PRINT_QUEUE[printer_mac]:
                if q_job["token"] == job_token:
                    job = q_job
                    break

        if not job:
            frappe.log_error(
                f"Job not found: mac={printer_mac}, token={job_token}",
                "cloudprnt_job"
            )
            frappe.response['http_status_code'] = 404
            return "Job not found"

        frappe.logger().info(f"Fetching job {job_token} for printer {printer_mac} (type: {media_type})")

        # Generate content based on media type
        if media_type == "application/vnd.star.line":
            # Generate Star Line Mode hex
            hex_content = generate_star_line_job(job)

            # Convert hex to binary
            binary_content = bytes.fromhex(hex_content)

            # Set response
            frappe.response['type'] = 'binary'
            frappe.response['filecontent'] = binary_content
            frappe.response['content_type'] = 'application/vnd.star.line'

        elif media_type == "text/vnd.star.markup":
            # Generate Star Markup
            # Check if it's a test job (has test_markup) or regular invoice job
            if "test_markup" in job and job["test_markup"]:
                markup_content = job["test_markup"]
            else:
                markup_content = get_pos_invoice_markup(job["invoice"])

            # Set response
            frappe.response['type'] = 'page'
            frappe.response['content_type'] = 'text/vnd.star.markup'
            return markup_content

        else:
            frappe.log_error(f"Unsupported media type: {media_type}", "cloudprnt_job")
            frappe.response['http_status_code'] = 415
            return "Unsupported media type"

        # Create print log
        create_print_log(job["invoice"])

        # Remove job from queue
        PRINT_QUEUE[printer_mac].remove(job)
        if len(PRINT_QUEUE[printer_mac]) == 0:
            del PRINT_QUEUE[printer_mac]

        frappe.logger().info(f"Job {job_token} sent successfully")

    except Exception as e:
        frappe.log_error(f"Error in cloudprnt_job: {str(e)}", "cloudprnt_job")
        frappe.response['http_status_code'] = 500
        return str(e)


@frappe.whitelist(allow_guest=True, methods=['DELETE'])
def cloudprnt_delete():
    """
    CloudPRNT Delete Endpoint (DELETE)

    Printer confirms that the job has been printed successfully

    Expected JSON body:
    {
        "printerMAC": "00.11.62.12.34.56",
        "statusCode": "200 OK",
        "jobToken": "job-id"
    }

    Response JSON:
    {
        "message": "ok"
    }
    """
    try:
        # Parse JSON body
        data = frappe.request.get_json()

        if not data:
            return {"message": "No data"}

        # Extract info
        printer_mac_dots = data.get("printerMAC", "")
        status_code = data.get("statusCode", "")
        job_token = data.get("jobToken", "")

        # Normalize MAC
        printer_mac = normalize_mac_address(printer_mac_dots)

        frappe.logger().info(
            f"Print confirmation: printer={printer_mac}, token={job_token}, status={status_code}"
        )

        # Update printer status
        if printer_mac:
            update_printer_status(
                printer_mac,
                status_code=status_code,
                printing_in_progress=False
            )

        return {"message": "ok"}

    except Exception as e:
        frappe.log_error(f"Error in cloudprnt_delete: {str(e)}", "cloudprnt_delete")
        return {"message": "error", "error": str(e)}


# ============================================================================
# PUBLIC API - Add jobs to queue
# ============================================================================

@frappe.whitelist()
def add_print_job(invoice_name, printer_mac=None):
    """
    Add a print job to the queue

    :param invoice_name: POS Invoice name
    :param printer_mac: Printer MAC address (optional, uses default if not provided)
    :return: Success/error dict
    """
    try:
        # Validate invoice
        if not frappe.db.exists("POS Invoice", invoice_name):
            return {
                "success": False,
                "message": f"Invoice {invoice_name} not found"
            }

        # Get printer MAC
        if not printer_mac:
            # Get default printer
            default_printer = frappe.db.get_single_value("CloudPRNT Settings", "default_printer")
            if not default_printer:
                return {
                    "success": False,
                    "message": "No default printer configured"
                }

            # Get MAC from printer
            settings = frappe.get_single("CloudPRNT Settings")
            printer_row = None
            for printer in settings.printers:
                if printer.label == default_printer:
                    printer_row = printer
                    break

            if not printer_row:
                return {
                    "success": False,
                    "message": f"Printer {default_printer} not found"
                }

            printer_mac = printer_row.mac_address

        # Normalize MAC
        printer_mac = normalize_mac_address(printer_mac)

        if not printer_mac:
            return {
                "success": False,
                "message": "Invalid MAC address"
            }

        # Create job
        job = {
            "token": invoice_name,  # Use invoice name as token
            "invoice": invoice_name,
            "printer_mac": printer_mac,
            "timestamp": frappe.utils.now_datetime().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "pending",
            "media_types": ["application/vnd.star.line", "text/vnd.star.markup"]
        }

        # Add to queue
        if printer_mac not in PRINT_QUEUE:
            PRINT_QUEUE[printer_mac] = []

        PRINT_QUEUE[printer_mac].append(job)

        frappe.logger().info(f"Job added to queue: {invoice_name} for printer {printer_mac}")

        return {
            "success": True,
            "message": f"Job added to queue",
            "job_token": job["token"],
            "printer_mac": printer_mac,
            "queue_position": len(PRINT_QUEUE[printer_mac])
        }

    except Exception as e:
        frappe.log_error(f"Error adding print job: {str(e)}", "add_print_job")
        return {
            "success": False,
            "message": str(e)
        }


@frappe.whitelist()
def get_queue_status(printer_mac=None):
    """
    Get queue status for debugging

    :param printer_mac: Optional MAC address to filter
    :return: Queue status dict
    """
    try:
        if printer_mac:
            printer_mac = normalize_mac_address(printer_mac)
            return {
                "printer_mac": printer_mac,
                "jobs": PRINT_QUEUE.get(printer_mac, [])
            }
        else:
            return {
                "total_printers": len(PRINT_QUEUE),
                "queues": PRINT_QUEUE
            }
    except Exception as e:
        return {"error": str(e)}


@frappe.whitelist()
def clear_queue(printer_mac=None):
    """
    Clear print queue (for debugging)

    :param printer_mac: Optional MAC address to clear specific printer
    :return: Success message
    """
    try:
        if printer_mac:
            printer_mac = normalize_mac_address(printer_mac)
            if printer_mac in PRINT_QUEUE:
                del PRINT_QUEUE[printer_mac]
                return {"success": True, "message": f"Queue cleared for {printer_mac}"}
            else:
                return {"success": False, "message": "Printer not in queue"}
        else:
            PRINT_QUEUE.clear()
            return {"success": True, "message": "All queues cleared"}
    except Exception as e:
        return {"success": False, "message": str(e)}
