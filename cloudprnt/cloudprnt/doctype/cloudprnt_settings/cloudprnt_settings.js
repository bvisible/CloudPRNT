// Copyright (c) 2024, Neoffice and contributors
// For license information, please see license.txt

frappe.ui.form.on("CloudPRNT Settings", {
    refresh(frm) {
        // Note: update_printers() removed - printers are now updated automatically
        // by cloudprnt_server.py when they poll the server

        // Add "Discover Printers" button
        frm.add_custom_button(__('Découvrir Imprimantes'), function() {
            frappe.call({
                method: 'cloudprnt.printer_discovery.get_discovered_printers',
                callback: function(r) {
                    if (r.message && r.message.success) {
                        const printers = r.message.printers;

                        if (printers.length === 0) {
                            frappe.msgprint({
                                title: __('Aucune nouvelle imprimante'),
                                indicator: 'blue',
                                message: __('Aucune nouvelle imprimante détectée. Assurez-vous que l\'imprimante poll le serveur avec l\'URL correcte.')
                            });
                            return;
                        }

                        // Create dialog with discovered printers
                        const dialog = new frappe.ui.Dialog({
                            title: __('Imprimantes Découvertes'),
                            fields: [
                                {
                                    fieldtype: 'HTML',
                                    fieldname: 'printer_list'
                                }
                            ],
                            primary_action_label: __('Fermer'),
                            primary_action: function() {
                                dialog.hide();
                            }
                        });

                        // Build HTML table
                        let html = '<table class="table table-bordered">';
                        html += '<thead><tr>';
                        html += '<th>MAC Address</th>';
                        html += '<th>Type</th>';
                        html += '<th>IP</th>';
                        html += '<th>Polls</th>';
                        html += '<th>Action</th>';
                        html += '</tr></thead><tbody>';

                        printers.forEach(printer => {
                            html += '<tr>';
                            html += `<td><code>${printer.mac_address}</code></td>`;
                            html += `<td>${printer.client_type}</td>`;
                            html += `<td>${printer.ip_address}</td>`;
                            html += `<td>${printer.poll_count}</td>`;
                            html += `<td><button class="btn btn-sm btn-primary add-printer" data-mac="${printer.mac_address}">Ajouter</button></td>`;
                            html += '</tr>';
                        });

                        html += '</tbody></table>';

                        dialog.fields_dict.printer_list.$wrapper.html(html);

                        // Add click handlers
                        dialog.fields_dict.printer_list.$wrapper.find('.add-printer').on('click', function() {
                            const mac = $(this).data('mac');

                            // Prompt for label
                            frappe.prompt([
                                {
                                    label: __('Nom de l\'imprimante'),
                                    fieldname: 'label',
                                    fieldtype: 'Data',
                                    reqd: 1
                                }
                            ], function(values) {
                                frappe.call({
                                    method: 'cloudprnt.printer_discovery.add_discovered_printer',
                                    args: {
                                        mac_address: mac,
                                        label: values.label
                                    },
                                    callback: function(r) {
                                        if (r.message && r.message.success) {
                                            frappe.show_alert({
                                                message: __('Imprimante ajoutée avec succès!'),
                                                indicator: 'green'
                                            }, 3);
                                            dialog.hide();
                                            frm.reload_doc();
                                        } else {
                                            frappe.msgprint({
                                                title: __('Erreur'),
                                                indicator: 'red',
                                                message: r.message.message || __('Erreur lors de l\'ajout')
                                            });
                                        }
                                    }
                                });
                            }, __('Ajouter Imprimante'), __('Ajouter'));
                        });

                        dialog.show();
                    } else {
                        frappe.msgprint({
                            title: __('Erreur'),
                            indicator: 'red',
                            message: r.message ? r.message.message : __('Erreur lors de la récupération des imprimantes')
                        });
                    }
                }
            });
        });

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
