// Copyright (c) 2024, Neoffice and contributors
// For license information, please see license.txt

frappe.ui.form.on("CloudPRNT Settings", {
    refresh(frm) {
        frappe.call({
            method: "cloudprnt.cloudprnt.doctype.cloudprnt_printers.cloudprnt_printers.update_printers",
            callback: function (r) {
                if(r.message)
                    console.log(r.message);
                frm.refresh_field('printers');
            }
        });
    },
});
