import os
import frappe
import subprocess
import binascii
import json
from datetime import datetime
import time
from frappe.utils import get_bench_path
from frappe import as_unicode, get_traceback
import requests
import io
from PIL import Image
from io import BytesIO
import binascii


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
        error=traceback[:140],  # Truncate the message to avoid length error
        method=title,
        reference_doctype=reference_doctype,
        reference_name=reference_name,
    )
    neo_error_log.insert(ignore_permissions=True)
    frappe.db.commit()

class StarCloudPRNTStarLineModeJob:
    SLM_NEW_LINE_HEX = "0A"
    SLM_SET_EMPHASIZED_HEX = "1B45"
    SLM_CANCEL_EMPHASIZED_HEX = "1B46"
    SLM_SET_LEFT_ALIGNMENT_HEX = "1B1D6100"
    SLM_SET_CENTER_ALIGNMENT_HEX = "1B1D6101"
    SLM_SET_RIGHT_ALIGNMENT_HEX = "1B1D6102"
    SLM_FEED_FULL_CUT_HEX = "1B6402"
    SLM_FEED_PARTIAL_CUT_HEX = "1B6403"
    SLM_CODEPAGE_HEX = "1B1D74"
    SLM_OPEN_CASH_DRAWER_HEX = "1B70001450"
    SLM_SET_LINE_SPACING_HEX = "1B33"  #
    
    def __init__(self, printer_meta):
        self.printer_meta = printer_meta
        self.printer_mac = printer_meta['printerMAC']
        self.print_job_builder = ""
        self.set_codepage("1252")

    def str_to_hex(self, string):
        return ''.join(format(ord(c), '02x') for c in string).upper()
    
    def set_text_emphasized(self):
        self.print_job_builder += self.SLM_SET_EMPHASIZED_HEX
    
    def cancel_text_emphasized(self):
        self.print_job_builder += self.SLM_CANCEL_EMPHASIZED_HEX
    
    def set_text_left_align(self):
        self.print_job_builder += self.SLM_SET_LEFT_ALIGNMENT_HEX
    
    def set_text_center_align(self):
        self.print_job_builder += self.SLM_SET_CENTER_ALIGNMENT_HEX
    
    def set_text_right_align(self):
        self.print_job_builder += self.SLM_SET_RIGHT_ALIGNMENT_HEX
    
    def set_codepage(self, codepage):
        if codepage == "UTF-8":
            self.print_job_builder += "1b1d295502003001" + "1b1d295502004000"
        elif codepage == "1252":
            self.print_job_builder += self.SLM_CODEPAGE_HEX + "20"
        else:
            self.print_job_builder += self.SLM_CODEPAGE_HEX + codepage
    
    def add_nv_logo(self, keycode):
        self.print_job_builder += "1B1C70" + keycode + "00" + self.SLM_NEW_LINE_HEX
    
    def set_line_spacing(self, spacing):
        self.print_job_builder += f"{self.SLM_SET_LINE_SPACING_HEX}{spacing:02x}"

    def set_font_magnification(self, width, height):
        w = min(max(0, width - 1), 5)
        h = min(max(0, height - 1), 5)
        self.print_job_builder += f"1B69{w:02x}{h:02x}"
    
    def add_hex(self, hex):
        self.print_job_builder += hex
    
    def add_text(self, text):
        self.print_job_builder += self.str_to_hex(text)
    
    def add_text_line(self, text):
        self.print_job_builder += self.str_to_hex(text) + self.SLM_NEW_LINE_HEX
    
    def add_new_line(self, quantity):
        self.print_job_builder += self.SLM_NEW_LINE_HEX * quantity

    def add_aligned_text(self, left_text, right_text, total_width=48):
        """Ajoute une ligne de texte avec le texte de gauche aligné à gauche et le texte de droite aligné à droite."""
        left_text_hex = self.str_to_hex(left_text)
        right_text_hex = self.str_to_hex(right_text)
        
        # Calculate number of spaces needed
        num_spaces = total_width - (len(left_text) + len(right_text))
        if num_spaces < 0:
            num_spaces = 0
        
        spaces_hex = "20" * num_spaces  # "20" is the hex code for space
        line_hex = left_text_hex + spaces_hex + right_text_hex + self.SLM_NEW_LINE_HEX
        self.print_job_builder += line_hex
    
    def sound_buzzer(self, circuit, pulse_ms, delay_ms):
        circuit = min(max(1, int(circuit)), 2)
        pulse_param = min(max(0, int(pulse_ms / 20)), 255)
        delay_param = min(max(0, int(delay_ms / 20)), 255)
        command = f"1B1D07{circuit:02X}{pulse_param:02X}{delay_param:02X}"
        self.print_job_builder += command
    
    def set_text_highlight(self):
        self.print_job_builder += "1B34"
    
    def cancel_text_highlight(self):
        self.print_job_builder += "1B35"
    
    def add_qr_code(self, error_correction, cell_size, data):
        model = 2
        error_correction = min(max(0, error_correction), 3)
        cell_size = min(max(1, cell_size), 8)
        data_length = len(data)
        
        set_model = f"1B1D795330{model:02X}"
        set_error_level = f"1B1D795331{error_correction:02X}"
        set_cell_size = f"1B1D795332{cell_size:02X}"
        set_data_prefix = f"1B1D79443100{data_length % 256:02X}{data_length // 256:02X}"
        print_cmd = "1B1D7950"
        
        self.print_job_builder += (set_model + set_error_level + set_cell_size +
                                   set_data_prefix + self.str_to_hex(data) + print_cmd)
    
    def add_barcode(self, type, module, hri, height, data):
        if type < 0 or type > 13:
            return
        
        n2 = 2 if hri else 1
        n3 = module if type in [4, 5, 8, 9, 10, 11, 12, 13] else min(max(1, module - 1), 3)
        height = min(max(8, height), 255)
        
        print_bc = f"1B62{type:02X}{n2:02X}{n3:02X}{height:02X}" + self.str_to_hex(data) + "1E"
        self.print_job_builder += print_bc
    
    def add_image_from_url(self, url):
            response = requests.get(url)
            image = Image.open(io.BytesIO(response.content))
            image = image.convert("1")  # Convert to 1-bit image for printing

            width, height = image.size
            pixels = list(image.getdata())

            # Convert image data to hex
            image_hex = ""
            for i in range(0, len(pixels), 8):
                byte = 0
                for bit in range(8):
                    if i + bit < len(pixels) and pixels[i + bit] == 0:  # 0 means black in 1-bit image
                        byte |= (1 << (7 - bit))
                image_hex += f"{byte:02X}"

            # Add image data to print job
            self.print_job_builder += f"1B2A{chr(width % 256)}{chr(width // 256)}{chr(height % 256)}{chr(height // 256)}{image_hex}"

    def cut(self):
        self.print_job_builder += self.SLM_FEED_PARTIAL_CUT_HEX

@frappe.whitelist()
def call_execute_cputil(command, args):
    path = os.path.join(get_bench_path(), "apps", "cloudprnt", "cloudprnt", "cputil", "cputil")
    # Parse args from JSON string to list
    try:
        args_list = json.loads(args)
    except json.JSONDecodeError as e:
        raise Exception(f"Invalid JSON in args: {args}") from e
    
    # Construire la commande avec les arguments inclus directement
    args_str = ' '.join(args_list)
    cmd = f'{path} {command} "{args_str}"'
    
    env = os.environ.copy()
    env['DOTNET_SYSTEM_GLOBALIZATION_INVARIANT'] = '1'

    result = subprocess.run(cmd, capture_output=True, text=True, shell=True, env=env)
    
    if result.returncode != 0:
        raise Exception(f"cputil failed: {result.stderr}")
    return result.stdout

@frappe.whitelist(allow_guest=True)
def process_order_history_from_php(order_history):
    lines = order_history.splitlines()

    for line in lines:
        try:
            doctype, docname, datetime_str = line.strip().split('|')
            # Remove .slt from datetime_str
            datetime_str = datetime_str.replace('.slt', '')
            # Extract only the necessary part of the datetime string
            datetime_part = datetime_str.split('_')[0]
            datetime_obj = datetime.strptime(datetime_part, "%d-%m-%Y %H:%M:%S")
            create_log_entry(doctype, docname, datetime_obj)
        except Exception as e:
            neolog("Error processing line", f"{line[:140]}\n{str(e)}", reference_doctype="Error Log")

def create_log_entry(doctype, docname, datetime_obj):
    if not frappe.db.exists(doctype, docname):
        neolog("Document Not Found", f"Could not find Document: {docname}", reference_doctype=doctype)
        return

    try:
        log_entry = frappe.get_doc({
            "doctype": "CloudPRNT Logs",
            "doctype_link": doctype,
            "document_link": docname,
            "datetime": datetime_obj
        })
        log_entry.insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception as e:
        neolog("Error creating log entry", str(e), reference_doctype=doctype, reference_name=docname)