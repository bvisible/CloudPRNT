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
    markup.append("[align: centre][font: a]")

    # Si un logo est configuré, ajoutez-le
    logo_url = frappe.db.get_single_value("CloudPRNT Settings", "header_logo_url")
    if logo_url:
        markup.append(f"[image: url {logo_url}; width 60%; min-width 48mm]")
        markup.append("[feed: length 1mm]")  # Space after logo

    # En-tête avec informations de la société
    markup.append(f"[magnify: width 2; height 1]{doc.company}[magnify]")
    
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

    markup.append(f"{_('Invoice')}: {doc.name}")
    
    # Format dates manually instead of using format_datetime
    posting_date_str = doc.posting_date
    if hasattr(doc.posting_date, 'strftime'):
        posting_date_str = doc.posting_date.strftime("%d-%m-%Y")
    markup.append(f"{_('Date')}: {posting_date_str}")
    
    posting_time_str = str(doc.posting_time)
    # Remove decimal seconds if present (e.g., "17:46:02.148083" -> "17:46:02")
    if '.' in posting_time_str:
        posting_time_str = posting_time_str.split('.')[0]
    markup.append(f"{_('Time')}: {posting_time_str}")
    
    markup.append(f"{_('Customer')}: {doc.customer_name}")
    markup.append(f"{_('Cashier')}: {owner_first_name} {owner_last_name[:1]}")
    markup.append(f"{_('Currency')}: {doc.currency}")

    # Separator before items
    markup.append("")  # Single blank line
    markup.append("[align: centre]")
    markup.append("-" * 48)
    markup.append("[align: left]")

    # Fonction pour normaliser les chaînes pour comparaison (supprimer espaces et tirets)
    def normalize_for_comparison(text):
        if not text:
            return ""
        # Convertir en minuscules, supprimer les espaces et les tirets
        return re.sub(r'[\s\-]', '', text.lower())

    # Articles - print each item with name and price together
    markup.append("")  # Single blank line
    for item in doc.items:
        markup.append("")  # Empty line for spacing between items

        # Display item name with item code on same line if different (in bold)
        markup.append("[bold: on]")
        item_display = item.item_name
        if normalize_for_comparison(item.item_code) != normalize_for_comparison(item.item_name):
            item_display = f"{item.item_name} ({item.item_code})"
        markup.append(item_display)
        markup.append("[bold: off]")

        # Display serial numbers and batch numbers if available
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

        # Show quantity x price with amount on same line, right-aligned
        qty_price = f"{item.qty} x {fmt_money(item.rate, currency=doc.currency)}"
        amount_str = fmt_money(item.amount, currency=doc.currency).strip()
        # Use rjust to align amount to the right (48 chars width)
        line = f"{qty_price} {amount_str.rjust(48 - len(qty_price) - 1)}"
        markup.append(line)

    # Ligne de séparation
    markup.append("")

    # Cartes-cadeaux générées
    for item in doc.items:
        if item.item_code == "giftcard":
            giftcard_numbers = frappe.get_all('Neoffice Giftcard', 
                filters={"pos_invoice": doc.name}, 
                fields=["name", "value"])
            
            for giftcard in giftcard_numbers:
                markup.append(f"{_('Gift card with a value of')} {fmt_money(giftcard.value, currency=doc.currency)}: {giftcard.name}")
    
    # Separator line (centered)
    markup.append("")
    markup.append("[align: centre]")
    markup.append("-" * 48)
    markup.append("[align: left]")
    markup.append("")

    # Totals and payments section - use spacing to align amounts on same line
    def format_line_with_amount(label, amount, width=48):
        """Format a line with label on left and amount on right, on same line"""
        amount_str = fmt_money(amount, currency=doc.currency).strip()
        label_str = f"{label}:"
        # Use rjust to right-align the amount within the total width
        line = f"{label_str} {amount_str.rjust(width - len(label_str) - 1)}"
        return line

    # Show subtotal
    if doc.discount_amount:
        markup.append(format_line_with_amount(_('Discount'), doc.discount_amount))

    if doc.taxes_and_charges == "TVA Vente HT - pri":
        markup.append(format_line_with_amount(_('Net Total'), doc.net_total))

    # Show tax lines
    for taxe in doc.taxes:
        if taxe.tax_amount != 0.0 and hasattr(taxe, 'account_head'):
            taxename = taxe.account_head.split('-')
            if len(taxename) > 1:
                tax_label = taxename[1].strip()
                markup.append(format_line_with_amount(tax_label, taxe.tax_amount))

    markup.append(format_line_with_amount(_('Grand Total'), doc.grand_total))

    if doc.rounded_total != doc.grand_total:
        markup.append(format_line_with_amount(_('Rounded Total'), doc.rounded_total))

    # Payment methods
    for payment in doc.payments:
        if payment.amount > 0:
            if payment.mode_of_payment == "Carte cadeau":
                payment_text = payment.mode_of_payment
                for payment_gift_card in doc.get("payment_gift_card", []):
                    payment_text += f" {_('N°')}: {payment_gift_card.code}"
                markup.append(format_line_with_amount(payment_text, payment.amount))
            else:
                markup.append(format_line_with_amount(payment.mode_of_payment, payment.amount))

    if doc.change_amount:
        markup.append(format_line_with_amount(_('Change Amount'), doc.change_amount))

    # Ligne de séparation
    markup.append("")
    markup.append("")

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
    
    # Display VAT information
    markup.append("[feed: length 3mm]")  # Space before Total product
    markup.append(f"{_('Total product')}: {doc.total_qty}")

    # Show VAT number once if there are any taxes
    tax_id_shown = False
    for taxe in doc.taxes:
        if taxe.tax_amount != 0.0 and hasattr(taxe, 'account_head'):
            if not tax_id_shown:
                tax_id = frappe.db.get_value("Company", doc.company, "tax_id")
                if tax_id:
                    markup.append(f"{_('VAT number')}: {tax_id}")
                    tax_id_shown = True

            taxename = taxe.account_head.split('-')
            if len(taxename) > 1:
                taxenameprint = taxename[1].strip()
                base_amount = tva_net.get(taxenameprint, 0)
                tax_amount = taxe.base_tax_amount_after_discount_amount
                total_amount = base_amount + tax_amount

                markup.append(f"{taxenameprint} {_('on')} {fmt_money(base_amount, currency=doc.currency)} = {fmt_money(tax_amount, currency=doc.currency)} | {_('Total')}: {fmt_money(total_amount, currency=doc.currency)}")
    
    # Conditions
    if doc.terms:
        markup.append("")
        markup.append("")
        markup.append(doc.terms)
    
    # Logo du bas
    footer_logo_url = frappe.db.get_single_value("CloudPRNT Settings", "footer_logo_url")
    if footer_logo_url:
        markup.append("[align: centre]")
        markup.append(f"[image: url {footer_logo_url}; width 40%; min-width 30mm]")

    # Ajouter un code-barres avec le numéro de facture
    markup.append("")
    markup.append("")
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