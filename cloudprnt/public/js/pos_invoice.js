frappe.ui.form.on('POS Invoice', {
    refresh: function(frm) {
        if (frm.doc.docstatus === 1) {  // Seulement pour les factures soumises
            frm.add_custom_button(__('Ticket Thermique'), function() {
                frappe.call({
                    method: 'cloudprnt.api.print_pos_invoice',
                    args: {
                        invoice_name: frm.doc.name
                    },
                    callback: function(r) {
                        if (r.message && r.message.success) {
                            frappe.show_alert({
                                message: __('Ticket envoyé à l\'imprimante'),
                                indicator: 'green'
                            }, 5);
                        }
                    }
                });
            }, __('Imprimer'));
        }
    }
}); 