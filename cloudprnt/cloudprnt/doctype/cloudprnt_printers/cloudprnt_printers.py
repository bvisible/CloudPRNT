# Copyright (c) 2024, Neoffice and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document
import frappe
import os
import json
from frappe import as_unicode, get_traceback
from datetime import datetime
from cloudprnt.print_job import call_execute_cputil
from frappe.utils import get_bench_path
import re

class CloudPRNTPrinters(Document):
    def __title(self):
        # Customize this to return the desired label
        return f"{self.label}"

@frappe.whitelist()
def update_printers():
    json_data_list = []
    path =  os.path.join(get_bench_path(), "apps", "cloudprnt", "cloudprnt", "cloudprnt", "php", "data", "printerdata")

    for root, dirs, files in os.walk(path):
        get_printer_data(json_data_list, root, files)
                    
    for printer in json_data_list:
        printer_line = frappe.db.get_all("CloudPRNT Printers", {"mac_address": printer['mac_address']})
        if printer_line:
            doc = frappe.get_doc("CloudPRNT Printers", printer_line[0].name)
            try:
                for key, value in printer.items():
                    if hasattr(doc, key):
                        setattr(doc, key, value)
                    else:
                        frappe.msgprint(f"Warning: {key} is not a valid field in the document.")
                doc.save()
                frappe.db.commit()  # Ensure changes are committed to the database
            except Exception as e:
                frappe.msgprint(f"An error occurred while updating the document: {e}")
        else:
            try:
                doc = frappe.new_doc("CloudPRNT Printers")
                doc.parent = "CloudPRNT Settings"
                doc.parenttype = "CloudPRNT Settings"
                doc.parentfield = "printers"
                for key, value in printer.items():
                    if hasattr(doc, key):
                        setattr(doc, key, value)
                    else:
                        frappe.msgprint(f"Warning: {key} is not a valid field in the document.")
                neolog("Creating new printer", doc)
                doc.insert()
                frappe.db.commit()  # Ensure changes are committed to the database
            except Exception as e:
                frappe.msgprint(f"An error occurred while creating the document: {e}")
    all
    return json_data_list

def get_printer_data(json_data_list, root, files):
    printer_data = {}
    for file in files:
        if file in ['data.json', 'communication.json', 'additional_communication.json']:
            file_path = os.path.join(root, file)
            with open(file_path, 'r') as json_file:
                data = json.load(json_file)
                if file == 'data.json':
                    printer_data['mac_address'] = data['macAddress']
                    printer_data['ip_address'] = data['ipAddress']
                    printer_data['last_activity'] = os.path.getmtime(file_path)
                elif file == 'communication.json':
                    printer_data['status'] = data['status']
                    printer_data['status_code'] = data['statusCode']
                    printer_data['printing_in_progress'] = data['printingInProgress']
                else:
                    client_action = data['clientAction']
                    for action in client_action:
                        if action['request'] == 'GetPollInterval':
                            printer_data['poll_interval'] = action['result']
    now_timestamp = datetime.now().timestamp()
    if "last_activity" in printer_data:
        printer_data['online'] = True if now_timestamp - printer_data['last_activity'] < int(printer_data['poll_interval'])+5 else False
    if "status" in printer_data:
        status = printer_data['status']
        status_details = json.loads(call_execute_cputil('jsonstatus', json.dumps(status.split(' '))))
        
        for key, value in status_details.items():
            if key != 'Online':
                snake_case = re.sub('([A-Z])', r'_\1', key).lower()
                # Remove the leading underscore if it exists
                if snake_case.startswith('_'):
                    snake_case = snake_case[1:]
                printer_data[snake_case] = value
    if printer_data:
        json_data_list.append(printer_data)

def neolog(title=None, message=None, reference_doctype=None, reference_name=None):
    """Log error to Error Log"""
    # Parameter ALERT:
    # the title and message may be swapped
    # the better API for this is log_error(title, message), and used in many cases this way
    # this hack tries to be smart about whats a title (single line ;-)) and fixes it

    traceback = None
    if message:
        if "\n" in title:  # traceback sent as title
            traceback, title = title, message
        else:
            traceback = message

    title = title or "Error"
    traceback = as_unicode(traceback or get_traceback(with_context=True))

    neo_error_log = frappe.get_doc(
        doctype="Error Log",
        error=traceback,
        method=title,
        reference_doctype=reference_doctype,
        reference_name=reference_name,
    )
    neo_error_log.insert(ignore_permissions=True)
    frappe.db.commit()
