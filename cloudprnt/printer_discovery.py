"""Printers that reach the site without being paired with it.

A printer whose CloudPRNT settings name this site but carry no credentials (or wrong ones) is answered "no
job" and noted here, so that the settings form can show it and pair it. The notes live in the cache, a day at
most, and are capped: anyone can post a poll, and a poll must not grow anything without limit.
"""

import json

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

from cloudprnt import guards
from cloudprnt.printers import SETTINGS, normalize_mac, plain_text, printer_for_mac

CACHE_KEY = "cloudprnt_unpaired_printers"
KEEP_HOURS = 24
MAX_NOTED = 50
#: What one note keeps of what the poll said: a Star printer's model and address fit; anything longer is cut.
MAX_TEXT = 140
MAX_ADDRESS = 45


def note_unpaired_printer(mac, ip_address=None, client_type=None, signed_in_as=None):
	"""Remember printer `mac` polled without valid credentials.

	Fifty notes of a few hundred characters at most: a guest's poll used to store its clientType whole, so a few
	large posts filled the one cache value that every later unpaired poll reads and writes back (audit of 06.11)."""
	mac = normalize_mac(mac)
	if not mac:
		return
	ip_address = plain_text(ip_address, MAX_ADDRESS)
	client_type = plain_text(client_type, MAX_TEXT)
	signed_in_as = plain_text(signed_in_as, MAX_TEXT)
	noted = _read()
	entry = noted.get(mac) or {"mac_address": mac, "first_seen": now_datetime().isoformat(), "poll_count": 0}
	entry.update(
		{
			"last_seen": now_datetime().isoformat(),
			"ip_address": ip_address or entry.get("ip_address"),
			"client_type": client_type or entry.get("client_type") or "",
			"poll_count": int(entry.get("poll_count") or 0) + 1,
			"signed_in_as": signed_in_as or "",
		}
	)
	noted[mac] = entry
	if len(noted) > MAX_NOTED:
		for old in sorted(noted, key=lambda key: noted[key].get("last_seen") or "")[: len(noted) - MAX_NOTED]:
			noted.pop(old, None)
	frappe.cache().set_value(CACHE_KEY, json.dumps(noted), expires_in_sec=KEEP_HOURS * 3600)


def _read():
	try:
		noted = json.loads(frappe.cache().get_value(CACHE_KEY) or "{}")
	except (TypeError, ValueError):
		return {}
	if not isinstance(noted, dict):
		return {}
	cutoff = add_to_date(now_datetime(), hours=-KEEP_HOURS)
	kept = {}
	for mac, entry in noted.items():
		if not isinstance(entry, dict) or not normalize_mac(mac):
			continue
		try:
			if get_datetime(entry.get("last_seen") or "2000-01-01") <= cutoff:
				continue
		except Exception:
			continue
		# Notes written before their texts were bounded are bounded as they are read, and so when written back.
		kept[normalize_mac(mac)] = {
			"mac_address": normalize_mac(mac),
			"first_seen": plain_text(entry.get("first_seen"), 32),
			"last_seen": plain_text(entry.get("last_seen"), 32),
			"ip_address": plain_text(entry.get("ip_address"), MAX_ADDRESS),
			"client_type": plain_text(entry.get("client_type"), MAX_TEXT),
			"poll_count": cint(entry.get("poll_count")),
			"signed_in_as": plain_text(entry.get("signed_in_as"), MAX_TEXT),
		}
	return kept


@frappe.whitelist()
def get_discovered_printers():
	"""The printers that polled the site in the last day without valid credentials, newest first."""
	guards.require("CloudPRNT Settings", "write")
	printers = []
	for entry in sorted(_read().values(), key=lambda entry: entry.get("last_seen") or "", reverse=True):
		row = printer_for_mac(entry["mac_address"])
		entry["configured_as"] = row.label if row else ""
		printers.append(entry)
	return {"success": True, "printers": printers}


@frappe.whitelist()
def add_discovered_printer(mac_address: str, label: str | None = None):
	"""Add a printer row for a discovered MAC address (its credentials are issued from the settings form)."""
	guards.require("CloudPRNT Settings", "write")
	mac = normalize_mac(mac_address)
	if not mac:
		frappe.throw(_("Invalid MAC address: {0}").format(mac_address))
	row = printer_for_mac(mac)
	if row:
		return {"success": True, "printer": row.name, "message": _("This printer is already configured")}
	settings = frappe.get_doc(SETTINGS)
	added = settings.append("printers", {"mac_address": mac, "label": label or mac})
	settings.save()
	return {"success": True, "printer": added.name}


def forget(mac):
	noted = _read()
	if noted.pop(normalize_mac(mac) or "", None) is not None:
		frappe.cache().set_value(CACHE_KEY, json.dumps(noted), expires_in_sec=KEEP_HOURS * 3600)

