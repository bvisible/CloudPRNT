# Copyright (c) 2023, Neoffice and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document
import frappe
import os
from datetime import datetime
from cloudprnt.print_job import StarCloudPRNTStarLineModeJob, neolog


class CloudPRNTSettings(Document):
	def validate(self):
		"""Validate settings"""
		# Vérifier que les URLs des logos sont valides
		if self.header_logo_url and not self.header_logo_url.startswith(('http://', 'https://')):
			frappe.msgprint("L'URL du logo d'en-tête doit commencer par http:// ou https://")
		
		if self.footer_logo_url and not self.footer_logo_url.startswith(('http://', 'https://')):
			frappe.msgprint("L'URL du logo de pied de page doit commencer par http:// ou https://")

@frappe.whitelist()
def get_settings():
	"""Return CloudPRNT settings as a dict"""
	settings = frappe.get_single("CloudPRNT Settings")
	return {
		"header_logo_url": settings.header_logo_url or "",
		"footer_logo_url": settings.footer_logo_url or "",
		"default_printer": settings.default_printer or "",
		"enable_auto_print": settings.enable_auto_print or 0,
		"default_paper_width": settings.default_paper_width or "80mm"
	}

@frappe.whitelist()
def test_print(printer, test_text="Test d'impression CloudPRNT"):
	"""
	Test print function using new Python CloudPRNT server

	Version 2.0 - Uses in-memory queue instead of writing to disk

	:param printer: Printer label or MAC address
	:param test_text: Text to print (default: "Test d'impression CloudPRNT")
	:return: Result dict with success status
	"""
	try:
		if not printer:
			return {"success": False, "message": "Aucune imprimante spécifiée"}

		# Get MAC address
		mac_address = None
		if ":" in printer or "." in printer:
			# MAC address provided directly
			mac_address = printer.replace(".", ":")
		else:
			# Printer label or name provided - find in settings
			settings = frappe.get_single("CloudPRNT Settings")
			for p in settings.printers:
				# Check both label and name (for backward compatibility)
				if p.label == printer or p.name == printer:
					mac_address = p.mac_address
					break

			if not mac_address:
				return {"success": False, "message": f"Imprimante {printer} non trouvée"}

		# Create test markup
		current_time = datetime.now().strftime("%d-%m-%Y %H:%M:%S")

		test_markup = f"""[align: centre]
[magnify: width 2; height 1]--- TEST D'IMPRESSION ---[magnify]

{test_text}

Date/Heure: {current_time}

[align: left]
Imprimante: {printer}
MAC: {mac_address}

[cut: feed; partial]"""

		# Create temporary test job document in memory
		test_job_token = f"TEST-{datetime.now().strftime('%Y%m%d%H%M%S')}"

		# Add to database queue (shared between processes)
		from cloudprnt.print_queue_manager import add_job_to_queue

		result = add_job_to_queue(
			job_token=test_job_token,
			printer_mac=mac_address,
			invoice_name=None,  # No invoice for test
			job_data=test_markup,  # Custom markup for test
			media_types=["application/vnd.star.line", "text/vnd.star.markup"]
		)

		if not result.get("success"):
			return result

		frappe.logger().info(f"Test print job added to queue: {test_job_token} for {mac_address}")

		return {
			"success": True,
			"message": f"Test d'impression envoyé à la queue (job: {test_job_token})",
			"job_token": test_job_token,
			"queue_position": result.get("queue_position", 1)
		}

	except Exception as e:
		frappe.log_error(message=str(e), title="Error in test_print")
		return {"success": False, "message": str(e)}