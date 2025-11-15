"""
CloudPRNT Print Queue Manager
==============================

Database-backed print queue for sharing jobs between processes.
Replaces in-memory PRINT_QUEUE for multi-process compatibility.
"""

import frappe
import json
from datetime import datetime


def add_job_to_queue(job_token, printer_mac, invoice_name=None, job_data=None, media_types=None):
	"""
	Add a print job to the database queue

	:param job_token: Unique job identifier
	:param printer_mac: Printer MAC address
	:param invoice_name: POS Invoice name (optional)
	:param job_data: Job data for custom jobs (optional)
	:param media_types: Supported media types
	:return: Success dict
	"""
	try:
		# Ensure we have a user set
		if not frappe.session.user:
			frappe.set_user("Administrator")

		# Default media types
		if not media_types:
			media_types = ["image/png", "application/vnd.star.line", "text/vnd.star.markup"]

		# Create queue entry using direct SQL to avoid module loading issues
		frappe.db.sql("""
			INSERT INTO `tabCloudPRNT Print Queue`
			(name, creation, modified, modified_by, owner, docstatus, idx,
			 job_token, printer_mac, invoice_name, status, job_data, media_types)
			VALUES
			(%(name)s, NOW(), NOW(), %(user)s, %(user)s, 0, 0,
			 %(job_token)s, %(printer_mac)s, %(invoice_name)s, 'Pending', %(job_data)s, %(media_types)s)
		""", {
			'name': frappe.generate_hash(length=10),
			'user': frappe.session.user or 'Administrator',
			'job_token': job_token,
			'printer_mac': printer_mac.upper(),
			'invoice_name': invoice_name,
			'job_data': job_data,
			'media_types': json.dumps(media_types)
		})
		frappe.db.commit()


		return {
			"success": True,
			"job_token": job_token,
			"queue_position": get_queue_position(printer_mac, job_token)
		}

	except Exception as e:
		frappe.log_error(f"Error adding job to queue: {str(e)}", "add_job_to_queue")
		return {
			"success": False,
			"message": str(e)
		}


def get_next_job(printer_mac):
	"""
	Get next pending job for a printer

	:param printer_mac: Printer MAC address
	:return: Job dict or None
	"""
	try:
		# Get oldest pending job for this printer
		jobs = frappe.get_all(
			"CloudPRNT Print Queue",
			filters={
				"printer_mac": printer_mac.upper(),
				"status": "Pending"
			},
			fields=["name", "job_token", "invoice_name", "job_data", "media_types", "printer_mac", "creation"],
			order_by="creation asc",
			limit=1
		)

		if not jobs:
			return None

		job = jobs[0]

		# Parse media types
		try:
			media_types = json.loads(job.get("media_types") or "[]")
		except:
			media_types = ["image/png", "application/vnd.star.line", "text/vnd.star.markup"]

		return {
			"name": job.name,
			"token": job.job_token,
			"invoice": job.invoice_name,
			"job_data": job.job_data,
			"media_types": media_types,
			"printer_mac": job.printer_mac
		}

	except Exception as e:
		frappe.log_error(f"Error getting next job: {str(e)}", "get_next_job")
		return None


def mark_job_fetched(job_token):
	"""
	Mark job as fetched by printer

	:param job_token: Job token
	"""
	try:
		frappe.db.set_value("CloudPRNT Print Queue", {"job_token": job_token}, "status", "Fetched")
		frappe.db.commit()
	except Exception as e:
		frappe.log_error(f"Error marking job as fetched: {str(e)}", "mark_job_fetched")


def mark_job_printed(job_token):
	"""
	Mark job as printed (delete from queue)

	:param job_token: Job token
	"""
	try:
		# Find and delete the job by token (regardless of status)
		jobs = frappe.get_all(
			"CloudPRNT Print Queue",
			filters={"job_token": job_token},
			fields=["name"]
		)

		if jobs:
			frappe.delete_doc("CloudPRNT Print Queue", jobs[0].name, ignore_permissions=True)
			frappe.db.commit()
			return {"success": True}
		else:
			frappe.logger().warning(f"Job not found for deletion: {job_token}")
			return {"success": False, "message": "Job not found"}

	except Exception as e:
		frappe.log_error(f"Error marking job as printed: {str(e)}", "mark_job_printed")
		return {"success": False, "message": str(e)}


def get_queue_position(printer_mac, job_token):
	"""
	Get position of job in queue

	:param printer_mac: Printer MAC address
	:param job_token: Job token
	:return: Queue position (1-based)
	"""
	try:
		jobs = frappe.get_all(
			"CloudPRNT Print Queue",
			filters={
				"printer_mac": printer_mac.upper(),
				"status": "Pending"
			},
			fields=["job_token", "creation"],
			order_by="creation asc"
		)

		for idx, job in enumerate(jobs, 1):
			if job.job_token == job_token:
				return idx

		return 0

	except Exception as e:
		frappe.log_error(f"Error getting queue position: {str(e)}", "get_queue_position")
		return 0


@frappe.whitelist()
def get_queue_status(printer_mac=None):
	"""
	Get queue status for debugging

	:param printer_mac: Optional MAC address to filter
	:return: Queue status dict
	"""
	try:
		filters = {}
		if printer_mac:
			filters["printer_mac"] = printer_mac.upper()

		jobs = frappe.get_all(
			"CloudPRNT Print Queue",
			filters=filters,
			fields=["job_token", "printer_mac", "invoice_name", "status", "creation"],
			order_by="creation asc"
		)

		if printer_mac:
			return {
				"printer_mac": printer_mac.upper(),
				"jobs": jobs
			}
		else:
			return {
				"total_jobs": len(jobs),
				"jobs": jobs
			}

	except Exception as e:
		frappe.log_error(f"Error getting queue status: {str(e)}", "get_queue_status")
		return {"error": str(e)}


@frappe.whitelist()
def clear_queue(printer_mac=None):
	"""
	Clear print queue

	:param printer_mac: Optional MAC address to clear specific printer
	:return: Success dict
	"""
	try:
		filters = {}
		if printer_mac:
			filters["printer_mac"] = printer_mac.upper()

		frappe.db.delete("CloudPRNT Print Queue", filters)
		frappe.db.commit()

		return {
			"success": True,
			"message": "Queue cleared"
		}

	except Exception as e:
		frappe.log_error(f"Error clearing queue: {str(e)}", "clear_queue")
		return {
			"success": False,
			"message": str(e)
		}
