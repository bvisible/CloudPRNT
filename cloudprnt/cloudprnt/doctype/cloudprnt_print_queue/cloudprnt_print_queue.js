// Copyright (c) 2026, Neoffice and contributors
// For license information, please see license.txt

frappe.ui.form.on("CloudPRNT Print Queue", {
	refresh(frm) {
		if (!["Printed", "Failed"].includes(frm.doc.status)) return;
		frm.add_custom_button(__("Print again"), () => {
			frappe.call({
				method: "cloudprnt.print_queue_manager.print_again",
				args: { job: frm.doc.name },
				callback() {
					frappe.set_route("List", "CloudPRNT Print Queue");
				},
			});
		});
	},
});
