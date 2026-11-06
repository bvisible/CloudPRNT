// The till receipt of a submitted POS sale, printed on a CloudPRNT printer from the invoice's form.
for (const doctype of ["POS Invoice", "Sales Invoice"]) {
	frappe.ui.form.on(doctype, {
		refresh(frm) {
			if (frm.doc.docstatus !== 1) return;
			if (frm.doctype === "Sales Invoice" && !frm.doc.is_pos) return;
			frm.add_custom_button(
				__("Thermal receipt"),
				() => {
					frappe.call({
						method: "cloudprnt.api.print_receipt",
						args: { doctype: frm.doctype, name: frm.doc.name },
						callback(r) {
							if (r.message && r.message.success) {
								// The message names the printer by its label, the user's text: an alert inserts it as HTML.
								const message = frappe.utils.escape_html(r.message.message || "");
								frappe.show_alert({ message, indicator: "green" }, 5);
							}
						},
					});
				},
				__("Print")
			);
		},
	});
}
