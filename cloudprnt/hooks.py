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

after_migrate = ["cloudprnt.devices.ensure_role"]

# A printer able to use MQTT asks for "cloudprnt-setting.json" beside its CloudPRNT URL when it starts: under
# /api/method/ that is a method name, routed to the protocol's answer.
override_whitelisted_methods = {"cloudprnt-setting.json": "cloudprnt.protocol.server_setting"}

# POS Next calls these when a table order's lines go to the kitchen: each station's printer gets its ticket.
pos_next_kitchen_send = ["cloudprnt.kitchen_ticket.on_kitchen_send"]
