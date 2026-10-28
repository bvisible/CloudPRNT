"""Instant printing: a printer that can is told the moment a job is queued for it, instead of polling every few seconds.

Star's CloudPRNT Version MQTT, "Trigger POST" mode (mC-Print3 MCP31CI/CBI from firmware 5.1, TSP100IV from 2.2). Such a
printer asks its site for cloudprnt-setting.json when it starts and every hour (protocol.server_setting). With instant
printing on, the site answers with an MQTT broker, an account and a topic; the printer keeps a connection to the broker,
and when a job is queued for it the site asks the broker to publish {"title": "request-post"} on its topic: the printer
polls at once, over the same authenticated HTTPS as before. No print data ever passes through the broker. A printer that
cannot, or a site with instant printing off, keeps polling as before; a lost signal costs at most one poll interval.

The broker is the hosting platform's, reached through its API (site_config `cloudprnt_hub_url`), which this site proves
itself to with the credential the platform wrote into its site_config (`instance_credential_cloudprnt`). Without that
credential instant printing is unavailable and nothing changes.

Each printer has its own broker account and password: the password is kept encrypted on this site and handed only to
that printer, signed in with its own CloudPRNT account (devices.py), in cloudprnt-setting.json.
"""

import json

import frappe
from frappe.utils.password import get_decrypted_password, remove_encrypted_password, set_encrypted_password

from cloudprnt.printers import PRINTER, SETTINGS, normalize_mac, update_printer

DEFAULT_HUB_URL = "https://neoservice.neoffice.me"
CREDENTIAL_CONF = "instance_credential_cloudprnt"
CREDENTIAL_HEADER = "X-Instance-Credential"
HUB_METHOD = "/api/method/neoffice_devops.api.cloudprnt_broker."
HUB_TIMEOUT_SECONDS = 8
PASSWORD_FIELD = "broker_password"


def available():
	"""Whether this site can offer instant printing at all: the platform gave it its credential."""
	return bool(frappe.conf.get(CREDENTIAL_CONF))


def enabled():
	"""Whether instant printing is on: switched on in the settings, and available."""
	return bool(frappe.db.get_single_value(SETTINGS, "instant_printing")) and available()


def setting_for(printer):
	"""The cloudprnt-setting.json of `printer` (a printer row, its account signed in): MQTT in Trigger POST mode, or None
	when instant printing is not possible for it now (the printer then keeps polling)."""
	connection = _connection(printer) or _register(printer)
	password = _password(printer)
	if not connection or not password:
		return None
	return {
		"title": "star_cloudprnt_server_setting",
		"version": "1.0.0",
		"reloadIntervalMin": int(connection.get("reload_minutes") or 60),
		"serverSupportProtocol": ["HTTP", "MQTT"],
		"settingForMQTT": {
			"useTriggerPOST": True,
			"pollingTimeSec": int(connection.get("poll_seconds") or 30),
			"mqttConnectionSetting": {
				"hostName": connection["host"],
				"portNumber": int(connection["port"]),
				"useTls": True,
				"keepAliveSec": 60,
				"deviceSubscribeTopicName": connection["topic"],
				"authenticationSetting": {"username": printer.broker_account, "password": password},
			},
		},
	}


def signal(row):
	"""A job was just queued for printer `row`: once it is committed, have the broker tell the printer."""
	if not row.get("broker_account") or not enabled():
		return
	frappe.enqueue(
		"cloudprnt.broker.trigger_now",
		queue="short",
		enqueue_after_commit=True,
		mac=row.mac_address,
	)


def trigger_now(mac):
	"""Background job: ask the broker to signal printer `mac`. A failure is logged; the printer's own poll catches up."""
	try:
		_hub("trigger", mac)
	except Exception:
		frappe.log_error("CloudPRNT: the printer could not be signalled", frappe.get_traceback())


def forget(rows):
	"""Printer rows removed from the settings: their broker accounts go too (best effort, in the background)."""
	macs = [row.mac_address for row in rows if row.get("broker_account") and row.get("mac_address")]
	for row in rows:
		remove_encrypted_password(PRINTER, row.name, PASSWORD_FIELD)
	if macs and available():
		frappe.enqueue("cloudprnt.broker.forget_now", queue="short", enqueue_after_commit=True, macs=macs)


def forget_now(macs):
	for mac in macs:
		try:
			_hub("forget", mac)
		except Exception:
			frappe.log_error("CloudPRNT: a printer's broker account could not be removed", frappe.get_traceback())


def _register(printer):
	"""A broker account for `printer`, from the platform; its connection, or None when the platform did not give one."""
	mac = normalize_mac(printer.mac_address)
	if not mac or not available():
		return None
	try:
		answer = _hub("register", mac)
	except Exception:
		frappe.log_error("CloudPRNT: the printer could not be registered on the broker", frappe.get_traceback())
		return None
	if not isinstance(answer, dict) or not answer.get("account") or not answer.get("password"):
		return None
	connection = {key: answer.get(key) for key in ("host", "port", "topic", "poll_seconds", "reload_minutes")}
	if not connection["host"] or not connection["port"] or not connection["topic"]:
		return None
	set_encrypted_password(PRINTER, printer.name, answer["password"], PASSWORD_FIELD)
	values = {"broker_account": answer["account"], "broker_connection": json.dumps(connection)}
	update_printer(printer.name, values)
	printer.update(values)
	return connection


def _connection(printer):
	if not printer.get("broker_account"):
		return None
	try:
		connection = json.loads(printer.get("broker_connection") or "{}")
	except ValueError:
		return None
	return connection if connection.get("host") and connection.get("port") and connection.get("topic") else None


def _password(printer):
	return get_decrypted_password(PRINTER, printer.name, PASSWORD_FIELD, raise_exception=False)


def _hub(method, mac):
	import requests

	url = (frappe.conf.get("cloudprnt_hub_url") or DEFAULT_HUB_URL).rstrip("/") + HUB_METHOD + method
	if not url.startswith("https://"):
		# The site's credential and a printer's broker password travel in this request: never in clear.
		raise ValueError("cloudprnt_hub_url must be an https:// address")
	response = requests.post(
		url,
		data={"mac": mac},
		headers={CREDENTIAL_HEADER: frappe.conf.get(CREDENTIAL_CONF) or ""},
		timeout=HUB_TIMEOUT_SECONDS,
	)
	response.raise_for_status()
	return response.json().get("message")
