// Copyright (c) 2024, Neoffice and contributors
// For license information, please see license.txt

const SETTINGS_METHODS = "cloudprnt.cloudprnt.doctype.cloudprnt_settings.cloudprnt_settings";

frappe.ui.form.on("CloudPRNT Settings", {
	refresh(frm) {
		frm.set_intro(
			__("Printers are configured with their own credentials: use Add a printer, then follow the steps it shows."),
			"blue"
		);
		const group = __("Printers");
		frm.add_custom_button(__("Add a printer"), () => add_printer(frm), group);
		frm.add_custom_button(__("Printer credentials"), () => renew_credentials(frm), group);
		frm.add_custom_button(__("Detected printers"), () => show_detected(frm), group);
		frm.add_custom_button(__("Test print"), () => test_print(frm), group);
		frm.add_custom_button(__("Open the cash drawer"), () => open_drawer(frm), group);
	},
});

function printer_options(frm) {
	return (frm.doc.printers || []).map((row) => ({ label: row.label || row.mac_address, value: row.name }));
}

function pick_printer(frm, title, action_label, extra_fields, action) {
	const options = printer_options(frm);
	if (!options.length) {
		frappe.msgprint(__("Add a printer first."));
		return;
	}
	const dialog = new frappe.ui.Dialog({
		title,
		fields: [
			{
				fieldname: "printer",
				fieldtype: "Select",
				label: __("Printer"),
				options,
				reqd: 1,
				default: frm.doc.default_printer || options[0].value,
			},
			...(extra_fields || []),
		],
		primary_action_label: action_label,
		primary_action(values) {
			dialog.hide();
			action(values);
		},
	});
	dialog.show();
}

function add_printer(frm) {
	frappe.prompt(
		[{ fieldname: "label", fieldtype: "Data", label: __("Printer label"), reqd: 1, description: __("For example: Till 1") }],
		(values) => {
			frappe.call({
				method: `${SETTINGS_METHODS}.pair_printer`,
				args: { label: values.label },
				freeze: true,
				callback(r) {
					frm.reload_doc();
					if (r.message) show_credentials(r.message);
				},
			});
		},
		__("Add a printer"),
		__("Create")
	);
}

function renew_credentials(frm) {
	pick_printer(frm, __("Printer credentials"), __("Issue new credentials"), [
		{
			fieldtype: "HTML",
			options: `<p class="text-muted">${__(
				"The printer stops printing until it is configured with the new credentials."
			)}</p>`,
		},
	], (values) => {
		frappe.call({
			method: `${SETTINGS_METHODS}.pair_printer`,
			args: { printer: values.printer },
			freeze: true,
			callback(r) {
				frm.reload_doc();
				if (r.message) show_credentials(r.message);
			},
		});
	});
}

function show_credentials(credentials) {
	const dialog = new frappe.ui.Dialog({
		title: __("Configure the printer"),
		size: "large",
		fields: [
			{
				fieldtype: "HTML",
				options: `<p>${__(
					"From a computer on the same network as the printer, run this command. It finds the printer, asks for its administrator password and configures it:"
				)}</p>`,
			},
			{ fieldname: "setup_command", fieldtype: "Code", label: __("Command"), read_only: 1 },
			{
				fieldtype: "HTML",
				options: `<p>${__(
					"Or open the printer's web page (its IP address in a browser), sign in, open CloudPRNT and enter these three values, then Save and restart the printer:"
				)}</p>`,
			},
			{ fieldname: "url", fieldtype: "Data", label: __("Server URL"), read_only: 1 },
			{ fieldname: "user", fieldtype: "Data", label: __("User Name"), read_only: 1 },
			{ fieldname: "password", fieldtype: "Data", label: __("Password"), read_only: 1 },
			{
				fieldtype: "HTML",
				options: `<p class="text-muted">${__(
					"The password is shown only now. Once the printer signs in, it prints a ticket saying it is connected, and its model and paper width appear here by themselves."
				)}</p>`,
			},
		],
		primary_action_label: __("Done"),
		primary_action() {
			dialog.hide();
		},
	});
	dialog.set_values(credentials);
	dialog.show();
}

function show_detected(frm) {
	frappe.call({
		method: "cloudprnt.printer_discovery.get_discovered_printers",
		callback(r) {
			const printers = (r.message && r.message.printers) || [];
			if (!printers.length) {
				frappe.msgprint({
					title: __("Detected printers"),
					indicator: "blue",
					message: __("No printer reached this site without credentials in the last day."),
				});
				return;
			}
			const rows = printers
				.map(
					(p) => `<tr>
						<td><code>${frappe.utils.escape_html(p.mac_address)}</code></td>
						<td>${frappe.utils.escape_html(p.ip_address || "")}</td>
						<td>${frappe.utils.escape_html(p.configured_as || __("Not configured"))}</td>
						<td>${p.signed_in_as ? __("Wrong credentials") : __("No credentials")}</td>
						<td>${frappe.datetime.comment_when(p.last_seen)}</td>
					</tr>`
				)
				.join("");
			frappe.msgprint({
				title: __("Detected printers"),
				indicator: "orange",
				message: `<p>${__(
					"These printers poll this site without valid credentials. Add a printer, or issue its credentials again, then configure it with them."
				)}</p>
				<table class="table table-bordered"><thead><tr>
					<th>${__("MAC Address")}</th><th>${__("IP Address")}</th><th>${__("Printer")}</th>
					<th>${__("Problem")}</th><th>${__("Last Seen")}</th>
				</tr></thead><tbody>${rows}</tbody></table>`,
			});
		},
	});
}

function test_print(frm) {
	pick_printer(frm, __("Test print"), __("Print"), [
		{ fieldname: "test_text", fieldtype: "Small Text", label: __("Text"), default: __("CloudPRNT print test") },
		{ fieldname: "image_link", fieldtype: "Data", label: __("Image link"), description: __("Optional: the URL of an image to print") },
	], (values) => {
		frappe.call({
			method: `${SETTINGS_METHODS}.test_print`,
			args: values,
			callback(r) {
				const ok = r.message && r.message.success;
				frappe.show_alert({ message: r.message && r.message.message, indicator: ok ? "green" : "red" }, 5);
			},
		});
	});
}

function open_drawer(frm) {
	pick_printer(frm, __("Open the cash drawer"), __("Open"), [], (values) => {
		frappe.call({
			method: "cloudprnt.api.open_cash_drawer",
			args: { printer: values.printer },
			callback(r) {
				if (r.message) frappe.show_alert({ message: r.message.message, indicator: "green" }, 5);
			},
		});
	});
}
