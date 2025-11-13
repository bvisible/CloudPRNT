// Copyright (c) 2024, Neoffice and contributors
// For license information, please see license.txt

frappe.ui.form.on("CloudPRNT Settings", {
    refresh(frm) {
        // Note: update_printers() removed - printers are now updated automatically
        // by cloudprnt_server.py when they poll the server

        frm.add_custom_button(__('Tester Imprimante'), function() {
            const dialog = new frappe.ui.Dialog({
                title: __('Test d\'impression'),
                fields: [
                    {
                        label: __('Imprimante'),
                        fieldname: 'printer',
                        fieldtype: 'Link',
                        options: 'CloudPRNT Printers',
                        reqd: 1,
                        default: frm.doc.default_printer
                    },
                    {
                        label: __('Texte à imprimer'),
                        fieldname: 'test_text',
                        fieldtype: 'Data',
                        default: 'Ceci est un test d\'impression CloudPRNT'
                    }
                ],
                primary_action_label: __('Imprimer'),
                primary_action: function(values) {
                    frappe.call({
                        method: 'cloudprnt.cloudprnt.doctype.cloudprnt_settings.cloudprnt_settings.test_print',
                        args: {
                            printer: values.printer,
                            test_text: values.test_text
                        },
                        callback: function(r) {
                            if (r.message && r.message.success) {
                                frappe.show_alert({
                                    message: __('Test d\'impression envoyé à l\'imprimante'),
                                    indicator: 'green'
                                }, 5);
                            } else {
                                frappe.show_alert({
                                    message: __('Erreur: ') + (r.message ? r.message.message : __('Erreur inconnue')),
                                    indicator: 'red'
                                }, 5);
                            }
                        }
                    });
                    dialog.hide();
                }
            });
            dialog.show();
        });
    },
});
