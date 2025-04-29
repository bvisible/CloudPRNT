import frappe
from frappe import _
from frappe.utils import fmt_money
import json
import os
import re
from datetime import datetime

def get_pos_invoice_markup(invoice_name):
    """
    Generate Star Document Markup for a POS Invoice
    :param invoice_name: Name of the POS Invoice
    :return: String containing Star Document Markup
    """
    doc = frappe.get_doc("POS Invoice", invoice_name)
    company_address = get_address_company(doc.company)
    owner_first_name = frappe.db.get_value("User", doc.owner, "first_name") or ""
    owner_last_name = frappe.db.get_value("User", doc.owner, "last_name") or ""
    
    # Commence à construire le markup
    markup = []
    
    # Logo et en-tête
    markup.append("[align: centre][font: a]\\")
    
    # Si un logo est configuré, ajoutez-le
    logo_url = frappe.db.get_single_value("CloudPRNT Settings", "header_logo_url")
    if logo_url:
        markup.append(f"[image: url {logo_url}; width 60%; min-width 48mm]\\")
    
    # En-tête avec informations de la société
    markup.append(f"[magnify: width 2; height 1]{doc.company}")
    markup.append("[magnify]\\")
    
    if company_address:
        for address in company_address:
            address_lines = []
            if address.address_line1:
                address_lines.append(address.address_line1)
            if address.address_line2:
                address_lines.append(address.address_line2)
            if address.city:
                city_line = []
                if address.pincode:
                    city_line.append(address.pincode)
                city_line.append(address.city)
                address_lines.append(" ".join(city_line))
            markup.append(" ".join(address_lines))
    
    markup.append("\\")
    markup.append(f"{_('Invoice')}: {doc.name}")
    
    # Format dates manually instead of using format_datetime
    posting_date_str = doc.posting_date
    if hasattr(doc.posting_date, 'strftime'):
        posting_date_str = doc.posting_date.strftime("%d-%m-%Y")
    markup.append(f"{_('Date')}: {posting_date_str}")
    
    posting_time_str = doc.posting_time
    if hasattr(doc.posting_time, 'strftime'):
        posting_time_str = doc.posting_time.strftime("%H:%M:%S")
    markup.append(f"{_('Time')}: {posting_time_str}")
    
    markup.append(f"{_('Customer')}: {doc.customer_name}")
    markup.append(f"{_('Cashier')}: {owner_first_name} {owner_last_name[:1]}.")
    markup.append(f"{_('Currency')}: {doc.currency}")
    markup.append("\\")
    
    # Ligne de séparation
    markup.append("[feed: length 3mm]")
    
    # En-tête des colonnes - un seul titre pour les articles
    markup.append("[align: left]\\")
    
    # En-tête de colonnes pour Qty et Price avec traduction
    markup.append(f"[column: left {_('Qty')}; right {_('Price')}]")
    
    # Ligne de séparation pour les colonnes
    markup.append("-" * 42)  # Ligne de tirets pour séparer l'en-tête des données
    
    # Fonction pour normaliser les chaînes pour comparaison (supprimer espaces et tirets)
    def normalize_for_comparison(text):
        if not text:
            return ""
        # Convertir en minuscules, supprimer les espaces et les tirets
        return re.sub(r'[\s\-]', '', text.lower())
    
    # Articles
    for item in doc.items:
        markup.append("\\")
        # Mettre le nom de l'article en gras avec la commande bold
        markup.append(f"[bold: on]{item.item_name}[bold: off]")
        
        # Afficher le code article UNIQUEMENT s'il est différent du nom (en ignorant la casse, espaces et tirets)
        if normalize_for_comparison(item.item_code) != normalize_for_comparison(item.item_name):
            markup.append(f"{item.item_code}")
        
        # Afficher les numéros de série et de lot si disponibles
        if hasattr(item, 'serial_and_batch_bundle') and item.serial_and_batch_bundle:
            try:
                sabb_entries = frappe.get_doc("Serial and Batch Bundle", item.serial_and_batch_bundle).get("entries") or []
                for sabb in sabb_entries:
                    if hasattr(sabb, 'serial_no') and sabb.serial_no:
                        markup.append(f"{_('Serial number')}: {sabb.serial_no}")
                    if hasattr(sabb, 'batch_no') and sabb.batch_no:
                        markup.append(f"{_('Batch No')}: {sabb.batch_no}")
            except Exception:
                # Ignore errors if serial/batch info is not available
                pass
        
        # Prix, remise et total de la ligne - sans répéter les en-têtes
        markup.append(f"[column: left {item.qty}; right {fmt_money(item.rate, currency=doc.currency)}]")
        
        if item.discount_percentage > 0:
            markup.append(f"[column: left; right Disc: {item.discount_percentage}%]")
        
        # Afficher le total de la ligne UNIQUEMENT s'il est différent du prix unitaire
        # (c'est-à-dire si quantité > 1 ou s'il y a une remise)
        if item.qty != 1 or item.discount_percentage > 0 or item.rate != item.amount:
            markup.append(f"[column: left; right {_('Total')}: {fmt_money(item.amount, currency=doc.currency)}]")
    
    # Ligne de séparation
    markup.append("[feed: length 1mm]")
    
    # Cartes-cadeaux générées
    for item in doc.items:
        if item.item_code == "giftcard":
            giftcard_numbers = frappe.get_all('Neoffice Giftcard', 
                filters={"pos_invoice": doc.name}, 
                fields=["name", "value"])
            
            for giftcard in giftcard_numbers:
                markup.append(f"{_('Gift card with a value of')} {fmt_money(giftcard.value, currency=doc.currency)}: {giftcard.name}")
    
    # Ligne de séparation
    markup.append("[feed: length 1mm]")
    
    # Total et paiements
    if doc.discount_amount:
        markup.append(f"[column: left {_('Discount')}; right {fmt_money(doc.discount_amount, currency=doc.currency)}]")
    
    if doc.taxes_and_charges == "TVA Vente HT - pri":
        markup.append(f"[column: left {_('Net Total')}; right {fmt_money(doc.net_total, currency=doc.currency)}]")
    
    markup.append(f"[column: left {_('Grand Total')}; right {fmt_money(doc.grand_total, currency=doc.currency)}]")
    
    if doc.rounded_total != doc.grand_total:
        markup.append(f"[column: left {_('Rounded Total')}; right {fmt_money(doc.rounded_total, currency=doc.currency)}]")
    
    # Méthodes de paiement
    for payment in doc.payments:
        if payment.amount > 0:
            if payment.mode_of_payment == "Carte cadeau":
                payment_text = payment.mode_of_payment
                for payment_gift_card in doc.get("payment_gift_card", []):
                    payment_text += f" {_('N°')}: {payment_gift_card.code}"
                markup.append(f"[column: left {payment_text}; right -{fmt_money(payment.amount, currency=doc.currency)}]")
            else:
                markup.append(f"[column: left {payment.mode_of_payment}; right -{fmt_money(payment.amount, currency=doc.currency)}]")
    
    markup.append(f"[column: left {_('Paid Amount')}; right {fmt_money(doc.paid_amount, currency=doc.currency)}]")
    
    if doc.change_amount:
        markup.append(f"[column: left {_('Change Amount')}; right {fmt_money(doc.change_amount, currency=doc.currency)}]")
    
    # Ligne de séparation
    markup.append("[feed: length 3mm]")
    
    # TVA
    tva_net = {}
    for taxe in doc.taxes:
        taxename = taxe.account_head.split('-')
        if len(taxename) > 1:
            tva_net[taxename[1].strip()] = 0
    
    for row in doc.items:
        if row.item_tax_template:
            compare_tva = row.item_tax_template.split(" - ")[0]
            for key in tva_net:
                if key.strip() in compare_tva.strip():
                    tva_net[key] = tva_net[key] + row.net_amount
    
    # Afficher les informations de TVA
    markup.append(f"{_('Total product')}: {doc.total_qty}")
    
    for taxe in doc.taxes:
        if taxe.tax_amount != 0.0 and hasattr(taxe, 'account_head'):
            if 'tax_id' not in locals():
                tax_id = frappe.db.get_value("Company", doc.company, "tax_id")
                if tax_id:
                    markup.append(f"{_('VAT number')}: {tax_id}")
            
            taxename = taxe.account_head.split('-')
            if len(taxename) > 1:
                taxenameprint = taxename[1].strip()
                base_amount = tva_net.get(taxenameprint, 0)
                tax_amount = taxe.base_tax_amount_after_discount_amount
                total_amount = base_amount + tax_amount
                
                markup.append(f"{taxenameprint} {_('on')} {fmt_money(base_amount, currency=doc.currency)} = {fmt_money(tax_amount, currency=doc.currency)} | {_('Total')}: {fmt_money(total_amount, currency=doc.currency)}")
    
    # Conditions
    if doc.terms:
        markup.append("[feed: length 3mm]")
        markup.append(doc.terms)
    
    # Logo du bas
    footer_logo_url = frappe.db.get_single_value("CloudPRNT Settings", "footer_logo_url")
    if footer_logo_url:
        markup.append("[align: centre]\\")
        markup.append(f"[image: url {footer_logo_url}; width 40%; min-width 30mm]")
    
    # Ajouter un code-barres avec le numéro de facture
    markup.append("[feed: length 3mm]")
    markup.append("[align: centre]")
    # Selon la doc, l'ajout d'un code-barre est fait avec cette syntaxe
    markup.append(f"[barcode: type code128; data {doc.name}; height 15mm; module 2; hri]")
    markup.append("[align: left]")
    
    # Coupe du ticket
    markup.append("[cut: feed; partial]")
    
    return "\n".join(markup)

def get_address_company(company):
    """Get address for the company"""
    return frappe.get_all(
        "Address",
        filters={
            "is_your_company_address": 1,
            "link_doctype": "Company",
            "link_name": company
        },
        fields=["address_line1", "address_line2", "city", "pincode"]
    ) 