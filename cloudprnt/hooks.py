from . import __version__ as app_version

app_name = "cloudprnt"
app_title = "CloudPRNT"
app_publisher = "Neoffice"
app_description = "Star CloudPRNT Integration for Frappe"
app_icon = "octicon octicon-file-directory"
app_color = "grey"
app_email = "hello@neoservice.ai"
app_license = "MIT"

# The thermal receipt button of a submitted POS sale.
doctype_js = {
	"POS Invoice": "public/js/receipt.js",
	"Sales Invoice": "public/js/receipt.js",
}

# A POS sale prints its receipt on its till's printer, where that printer is set to (CloudPRNT Printers.auto_print).
doc_events = {
	"Sales Invoice": {"on_submit": "cloudprnt.api.print_on_submit"},
	"POS Invoice": {"on_submit": "cloudprnt.api.print_on_submit"},
}

scheduler_events = {
	"all": [
		"cloudprnt.print_queue_manager.fail_unconfirmed_jobs",
		"cloudprnt.print_queue_manager.expire_jobs",
		"cloudprnt.printers.mark_silent_printers_offline",
	],
	"daily": ["cloudprnt.print_queue_manager.delete_old_jobs"],
}

# Star's cputil is fetched from Star and pinned by sha256: this public repository does not ship it (cputil_install.py).
after_install = ["cloudprnt.cputil_install.install"]
after_migrate = ["cloudprnt.devices.ensure_role", "cloudprnt.cputil_install.install"]

# A printer able to use MQTT asks for "cloudprnt-setting.json" beside its CloudPRNT URL when it starts: under
# /api/method/ that is a method name, routed to the protocol's answer.
override_whitelisted_methods = {"cloudprnt-setting.json": "cloudprnt.protocol.server_setting"}

# POS Next calls these when a table order's lines go to the kitchen: each station's printer gets its ticket.
pos_next_kitchen_send = ["cloudprnt.kitchen_ticket.on_kitchen_send"]

# POS Next calls this when a table's draft order that had been at the kitchen is deleted: what the printers printed comes out cancelled.
pos_next_kitchen_void = ["cloudprnt.kitchen_ticket.on_kitchen_void"]

# POS Next calls these from the kitchen's and the runner's screens (their printer button): print a copy of a ticket, follow it,
# and tell which stations have a printer at all. A copy is not a send: it never changes what the next send prints.
pos_next_kitchen_reprint = ["cloudprnt.kitchen_ticket.on_kitchen_reprint"]
pos_next_kitchen_reprint_status = ["cloudprnt.kitchen_ticket.kitchen_copy_status"]
pos_next_kitchen_printers = ["cloudprnt.kitchen_ticket.stations_with_a_printer"]

# POS Next calls this when a table's QR code is printed (the Print button of the QR dialog): the table's name, a line to scan
# to order, the code and its link on the till's receipt printer. POS Next works the link out from the guest token itself;
# this app prints what it is handed.
pos_next_table_qr_print = ["cloudprnt.table_qr_ticket.on_table_qr_print"]
