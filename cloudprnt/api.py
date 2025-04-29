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
def print_pos_invoice(invoice_name, printer=None):
    """
    Print a POS Invoice using CloudPRNT - Fonction basée sur test_print qui fonctionne
    :param invoice_name: Name of the POS Invoice
    :param printer: MAC address of printer or CloudPRNT Printer document name
    :return: Success message
    """
    try:
        if not invoice_name:
            return {"success": False, "message": "Aucune facture spécifiée"}
        
        if not frappe.db.exists("POS Invoice", invoice_name):
            return {"success": False, "message": f"Facture POS {invoice_name} non trouvée"}
        
        # Get default printer if not specified
        if not printer:
            printer = frappe.db.get_single_value("CloudPRNT Settings", "default_printer")
        
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
        
        # Simplifier la structure des métadonnées d'imprimante
        printer_meta = {
            'printerMAC': printer_mac,
        }
        
        # Créer un job d'impression simple
        job = StarCloudPRNTStarLineModeJob(printer_meta)
        
        # Récupérer le markup pour la facture
        markup_text = get_pos_invoice_markup(invoice_name)
        
        # Traiter les lignes du markup
        lines = markup_text.split('\n')
        
        # Variables pour suivre l'état du formatage
        current_align = "left"  # Alignement par défaut
        
        # Fonction pour nettoyer complètement une ligne de toutes les balises
        def clean_all_tags(text):
            # Supprime toutes les balises de type [tag: content]
            text = re.sub(r'\[([^]]+)\]', '', text)
            return text
        
        # Parcourir chaque ligne et appliquer le formatage
        for line in lines:
            # Garder une copie originale de la ligne pour les traitements
            original_line = line
            
            # Traiter les balises d'alignement
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
            
            # Traiter les balises pour le texte en gras
            if "[magnify:" in original_line:
                job.set_text_emphasized()
            elif "[magnify]" in original_line:
                job.cancel_text_emphasized()
            
            # Traiter les sauts de ligne
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
            
            # Traiter la découpe du ticket
            if "[cut" in original_line:
                job.cut()
                continue  # Passer à la ligne suivante
            
            # Traiter les colonnes de texte
            if "[column: left" in original_line:
                match = re.search(r'\[column: left([^;]*); right([^]]*)\]', original_line)
                if match:
                    left_text = match.group(1).strip()
                    if left_text.startswith(":"):
                        left_text = left_text[1:].strip()
                    
                    right_text = match.group(2).strip()
                    if right_text.startswith(":"):
                        right_text = right_text[1:].strip()
                    
                    # Ajouter du texte aligné
                    job.add_aligned_text(left_text, right_text)
                    continue  # Passer à la ligne suivante
            
            # Nettoyer la ligne de toutes les balises restantes
            clean_line = clean_all_tags(original_line)
            
            # Traiter les lignes de texte normales
            if clean_line:
                # Si la ligne se termine par \ (continuation), ne pas ajouter de saut de ligne
                if clean_line.endswith("\\"):
                    job.add_text(clean_line[:-1])  # Supprimer le \
                else:
                    job.add_text_line(clean_line)
        
        # Date et heure simples
        current_time = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
        
        # Informations pour le travail d'impression
        info_content = f"POS Invoice|{invoice_name}|{current_time}.slt"
        
        # Envoyer le travail d'impression
        job.print_job(info_content)
        
        return {"success": True, "message": f"Impression de la facture {invoice_name} envoyée à l'imprimante {printer}"}
    except Exception as e:
        frappe.log_error(message=str(e), title="Error in print_pos_invoice")
        return {"success": False, "message": str(e)}