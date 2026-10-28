// Copyright (c) 2026, Neoffice and contributors
// For license information, please see license.txt

frappe.listview_settings["Cash Drawer Opening"] = {
	get_indicator(doc) {
		const colours = { Opened: "green", Sent: "orange", "Not opened": "red" };
		return [__(doc.result), colours[doc.result] || "gray", `result,=,${doc.result}`];
	},
};
