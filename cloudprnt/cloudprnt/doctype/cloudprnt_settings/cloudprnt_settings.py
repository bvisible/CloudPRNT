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
	Fonction simple pour tester l'impression
	:param printer: ID de l'imprimante CloudPRNT
	:param test_text: Texte à imprimer
	:return: Résultat du test
	"""
	try:
		if not printer:
			return {"success": False, "message": "Aucune imprimante spécifiée"}
		
		# Récupérer l'adresse MAC
		if not ":" in printer:
			if frappe.db.exists("CloudPRNT Printers", printer):
				mac_address = frappe.db.get_value("CloudPRNT Printers", printer, "mac_address")
			else:
				return {"success": False, "message": f"Imprimante {printer} non trouvée"}
		else:
			mac_address = printer
		
		# Remplacer les ":" par des "." comme dans l'exemple fonctionnel
		printer_mac = mac_address.replace(":", ".")
		
		# Simplifier la structure des métadonnées d'imprimante comme dans l'exemple
		printer_meta = {
			'printerMAC': printer_mac,
		}
		
		# Créer un job d'impression simple
		job = StarCloudPRNTStarLineModeJob(printer_meta)
		
		# Centrer le texte
		job.set_text_center_align()
		
		# Ajouter un titre en gras et plus grand
		job.set_text_emphasized()
		job.print_job_builder += job.str_to_hex("--- TEST D'IMPRESSION ---\n\n")
		job.cancel_text_emphasized()
		
		# Ajouter le texte de test
		job.print_job_builder += job.str_to_hex(f"{test_text}\n\n")
		
		# Ajouter la date et l'heure
		current_time = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
		job.print_job_builder += job.str_to_hex(f"Date/Heure: {current_time}\n\n")
		
		# Revenir à l'alignement gauche
		job.set_text_left_align()
		
		# Ajouter des informations sur l'imprimante
		job.print_job_builder += job.str_to_hex(f"Imprimante: {printer}\n")
		job.print_job_builder += job.str_to_hex(f"MAC: {mac_address}\n\n")
		
		# Couper le papier
		job.cut()
		
		# Créer les informations sur le travail d'impression
		info_content = f"Test|{printer}|{current_time}.slt"
		
		# Envoyer le travail d'impression
		job.print_job(info_content)
		
		return {"success": True, "message": "Test d'impression envoyé"}
	except Exception as e:
		frappe.log_error(message=str(e), title="Error in test_print")
		return {"success": False, "message": str(e)}