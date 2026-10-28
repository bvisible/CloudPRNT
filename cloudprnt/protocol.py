"""The Star CloudPRNT protocol, served by the site itself at /api/method/cloudprnt.protocol.serve.

A printer configured with that URL speaks to it in three ways:

* POST (the poll, every few seconds): its MAC address, its status, the answers to what the server asked it
  last time. The answer says whether a job is ready, in which formats and under which token; when none is,
  it may ask the printer what it is (model, firmware, formats it prints, paper), which is how the site
  configures itself to each printer.
* GET ?mac=&type=&token=: the job, in the format the printer chose among those offered.
* DELETE ?mac=&token=&code=: the job is done; the code says whether it printed.

Each printer signs in with its own account (devices.py): an anonymous poll from an unknown printer is noted
for pairing and answered "no job"; one from a printer paired with an account is asked for its credentials
(401), so that a printer that sends them only when challenged sends them. No job leaves the site for a caller
other than the account of the printer it is queued for.

This replaces a FastAPI server that ran beside the bench on port 8001 and read the site's database directly,
and an in-process version whose queue lived in the memory of one worker: the printer reached neither reliably.
"""

import json
from urllib.parse import unquote

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import now_datetime
from werkzeug.wrappers import Response

from cloudprnt import broker, render
from cloudprnt import print_queue_manager as jobs
from cloudprnt.printer_discovery import forget, note_unpaired_printer
from cloudprnt.printers import normalize_mac, printer_for_mac, printer_for_user, update_printer

#: What the site asks a printer about itself (CloudPRNT client actions), and when it asks again.
CAPABILITIES = ("ClientType", "ClientVersion", "Encodings", "PageInfo", "GetPollInterval")
CAPABILITIES_EVERY_SECONDS = 24 * 3600
ASK_AGAIN_SECONDS = 60
#: A printer's last-seen time is written at most this often while nothing else about it changes.
SEEN_EVERY_SECONDS = 60


@frappe.whitelist(allow_guest=True, methods=["POST", "GET", "DELETE"])
def serve():
	"""The one URL a printer is configured with (POST poll, GET job, DELETE confirmation)."""
	method = frappe.request.method
	try:
		if method == "POST":
			return _poll()
		if method == "GET":
			return _fetch()
		return _confirm()
	except Exception:
		frappe.db.rollback()
		frappe.log_error(f"CloudPRNT: {method} from a printer failed", frappe.get_traceback())
		if method == "POST":
			return _json({"jobReady": False})
		return _text("Server error", 500)


@frappe.whitelist(allow_guest=True, methods=["GET"])
@rate_limit(limit=60, seconds=60)
def server_setting():
	"""cloudprnt-setting.json: what a printer able to use MQTT (CloudPRNT Version MQTT: mC-Print3 MCP31CI/CBI from
	firmware 5.1, TSP100IV from 2.2) asks when it starts and every hour, at the folder of its URL with this file name
	(nginx and hooks.override_whitelisted_methods send it here, ?mac=…&replaced_path=…).

	A paired printer is challenged for its credentials, like its polls (the same answer whether instant printing is on or
	not). Signed in, it is noted as able to print instantly (instant_capable), and with instant printing on (broker.py)
	its own account receives the broker, its account and its topic, in Trigger POST mode. Otherwise 404, which is the
	answer Star specifies for "HTTP only": the printer polls. Nothing is written for an anonymous caller."""
	try:
		mac = normalize_mac(frappe.request.args.get("mac"))
		row = printer_for_mac(mac) if mac else None
		if row is None:
			return _text("CloudPRNT HTTP only", 404)
		printer = _signed_in_printer(mac)
		if printer is None:
			if row.device_user and frappe.session.user == "Guest":
				return _challenge()
			return _text("CloudPRNT HTTP only", 404)
		if not printer.instant_capable:
			update_printer(printer.name, {"instant_capable": 1})
			printer.instant_capable = 1
		setting = broker.setting_for(printer) if broker.enabled() else None
		frappe.db.commit()
		if not setting:
			return _text("CloudPRNT HTTP only", 404)
		return _json(setting)
	except Exception:
		frappe.db.rollback()
		frappe.log_error("CloudPRNT: cloudprnt-setting.json failed", frappe.get_traceback())
		return _text("CloudPRNT HTTP only", 404)


def _poll():
	body = _body()
	mac = normalize_mac(body.get("printerMAC"))
	if not mac:
		return _json({"jobReady": False}, 400)

	printer = _signed_in_printer(mac)
	if printer is None:
		return _unpaired(mac, body)

	first_contact = not printer.last_seen
	_record_answers(printer, body.get("clientAction"))
	_record_status(printer, body)
	if first_contact:
		_queue_connection_ticket(printer)
	frappe.db.commit()

	if body.get("printingInProgress"):
		return _json({"jobReady": False})
	job = jobs.next_job(printer)
	if job:
		return _json(
			{"jobReady": True, "mediaTypes": jobs.media_types_for(job, printer), "jobToken": job.job_token}
		)
	reply = {"jobReady": False}
	questions = _questions_for(printer)
	if questions:
		reply["clientAction"] = [{"request": question, "options": ""} for question in questions]
	return _json(reply)


def _fetch():
	args = frappe.request.args
	mac = normalize_mac(args.get("mac"))
	printer = _signed_in_printer(mac) if mac else None
	if printer is None:
		return _refused(mac)
	job = jobs.get_job(printer, args.get("token"))
	if not job:
		return _text("No such job", 404)
	media_type = (args.get("type") or "").strip()
	if media_type not in jobs.media_types_for(job, printer):
		return _text("Media type not offered for this job", 415)
	try:
		data = jobs.render_job(job, media_type, printer)
	except render.RenderError as error:
		frappe.log_error(f"CloudPRNT: job {job.job_token} could not be rendered", str(error))
		return _text("The job could not be rendered", 500)
	jobs.mark_fetched(job, media_type)
	frappe.db.commit()
	headers = {}
	drawer = jobs.drawer_for(job)
	if media_type in render.PRINTER_RENDERED and drawer:
		# A printer that renders the job itself opens the drawer when the job asks it to in a header.
		headers["X-Star-CashDrawer"] = drawer
	return Response(data, status=200, mimetype=media_type, headers=headers)


def _confirm():
	args = frappe.request.args
	mac = normalize_mac(args.get("mac"))
	printer = _signed_in_printer(mac) if mac else None
	if printer is None:
		return _refused(mac)
	job = jobs.get_job(printer, args.get("token"), statuses=("Fetched",))
	if job:
		jobs.mark_confirmed(job, args.get("code"))
		update_printer(printer.name, {"printing_in_progress": 0})
		frappe.db.commit()
	return _json({"message": "ok"})


def _signed_in_printer(mac):
	"""The printer row whose account signed this request in, if it is the printer of `mac`.

	A paired row without a MAC address yet takes the first MAC its account polls with.
	"""
	printer = printer_for_user(frappe.session.user)
	if printer is None:
		return None
	bound = normalize_mac(printer.mac_address)
	if bound:
		return printer if bound == mac else None
	holder = printer_for_mac(mac)
	if holder and holder.name != printer.name:
		return None
	update_printer(printer.name, {"mac_address": mac})
	printer.mac_address = mac
	forget(mac)
	return printer


def _unpaired(mac, body):
	signed_in_as = "" if frappe.session.user == "Guest" else frappe.session.user
	note_unpaired_printer(mac, frappe.local.request_ip, body.get("clientType"), signed_in_as)
	row = printer_for_mac(mac)
	if row and row.device_user and frappe.session.user == "Guest":
		# A paired printer that sent no credentials: challenge it, as a printer may wait for one to send them.
		return _challenge()
	return _json({"jobReady": False})


def _refused(mac):
	row = printer_for_mac(mac) if mac else None
	if row and row.device_user and frappe.session.user == "Guest":
		return _challenge()
	return _text("Forbidden", 403)


def _record_answers(printer, answers):
	"""Store what the printer answered to the site's questions (CloudPRNT client action results)."""
	values = {}
	for answer in answers if isinstance(answers, list) else []:
		if not isinstance(answer, dict):
			continue
		request, result = answer.get("request"), answer.get("result")
		if result in (None, ""):
			continue
		if request == "ClientType":
			values["client_type"] = str(result)[:140]
		elif request == "ClientVersion":
			values["client_version"] = str(result)[:140]
		elif request == "Encodings":
			values["media_types"] = json.dumps(_encodings(result))
		elif request == "PageInfo":
			values.update(_page_info(result))
		elif request == "GetPollInterval":
			try:
				values["poll_interval"] = int(float(result))
			except (TypeError, ValueError):
				pass
	if values:
		frappe.cache().set_value(f"cloudprnt_capabilities_{printer.name}", 1, expires_in_sec=CAPABILITIES_EVERY_SECONDS)
		update_printer(printer.name, values)
		printer.update(values)


def _encodings(result):
	if isinstance(result, str):
		parts = result.replace(",", ";").split(";")
	elif isinstance(result, list):
		parts = result
	else:
		parts = []
	return [str(part).strip() for part in parts if str(part).strip()]


def _page_info(result):
	if isinstance(result, str):
		try:
			result = json.loads(result)
		except ValueError:
			return {}
	if not isinstance(result, dict):
		return {}

	def number(key):
		try:
			return float(result.get(key))
		except (TypeError, ValueError):
			return 0

	values = {}
	if number("paperWidth"):
		values["paper_width"] = round(number("paperWidth"))
	if number("printWidth") and number("horizontalResolution"):
		values["print_dots"] = int(number("printWidth") * number("horizontalResolution"))
	return values


def _questions_for(printer):
	"""What to ask the printer about itself: everything once a day, sooner when something is missing, never
	more often than once a minute."""
	if frappe.cache().get_value(f"cloudprnt_asked_{printer.name}"):
		return []
	known = bool(frappe.cache().get_value(f"cloudprnt_capabilities_{printer.name}"))
	missing = not (printer.client_type and printer.media_types and printer.paper_width)
	if known and not missing:
		return []
	frappe.cache().set_value(f"cloudprnt_asked_{printer.name}", 1, expires_in_sec=ASK_AGAIN_SECONDS)
	return list(CAPABILITIES)


def _record_status(printer, body):
	"""Store the printer's status: what changed, and its last-seen time once a minute otherwise."""
	asb = str(body.get("status") or "").strip()
	code = unquote(str(body.get("statusCode") or "")).strip()
	busy = 1 if body.get("printingInProgress") else 0
	ip = frappe.local.request_ip
	key = f"cloudprnt_seen_{printer.name}"
	last = frappe.cache().get_value(key) or {}
	now = now_datetime()
	state = {"asb": asb, "code": code, "busy": busy, "ip": ip}
	unchanged = isinstance(last, dict) and all(last.get(k) == v for k, v in state.items())
	if unchanged and (now.timestamp() - float(last.get("written") or 0)) < SEEN_EVERY_SECONDS:
		return

	values = {"last_seen": now, "online": 1, "status_code": code[:140], "printing_in_progress": busy, "ip_address": ip}
	# The status is read again when the ASB changed, and when the printer comes back from being marked offline.
	if not (isinstance(last, dict) and last.get("asb") == asb) or not printer.online:
		values.update(_status_fields(render.decode_status(asb), code))
	update_printer(printer.name, values)
	frappe.cache().set_value(key, {**state, "written": now.timestamp()}, expires_in_sec=3600)


def _status_fields(decoded, code):
	if not decoded:
		ok = code[:1] == "2"
		return {"status": _("Ready") if ok else (code or _("Unknown"))}
	detail = decoded.get("Detail") or {}
	flags = {
		"cover_open": decoded.get("CoverOpen"),
		"paper_empty": decoded.get("PaperEmpty"),
		"paper_low": decoded.get("PaperLow"),
		"compulsion_switch": decoded.get("CompulsionSwitch"),
		"drawer_open": decoded.get("CompulsionSwitch"),
		"cutter_error": decoded.get("CutterError"),
		"mechanical_error": decoded.get("MechanicalError"),
		"over_temperature": decoded.get("OverTemperature"),
		"presenter_paper_jam": decoded.get("PresenterPaperJam"),
		"receive_buffer_overflow": decoded.get("ReceiveBufferOverflow"),
		"black_mark_error": decoded.get("BlackMarkError"),
		"voltage_error": decoded.get("VoltageError"),
		"recoverable": decoded.get("Recoverable"),
	}
	flags["drawer_connected"] = detail.get("ExternalDevive1Connected")  # sic: cputil's spelling
	values = {field: 1 if value else 0 for field, value in flags.items()}
	if detail.get("Drawer1OpenCloseSignal"):
		# The printer is set to read a closed drawer as a high signal: the switch reads the other way round.
		values["drawer_open"] = 0 if values["drawer_open"] else 1
	if values["cover_open"]:
		status = _("Cover open")
	elif values["paper_empty"]:
		status = _("Paper empty")
	elif any(values[field] for field in ("cutter_error", "mechanical_error", "over_temperature", "voltage_error")):
		status = _("Printer error")
	elif not decoded.get("Online"):
		status = _("Offline")
	elif values["paper_low"]:
		status = _("Paper low")
	else:
		status = _("Ready")
	values["status"] = status
	return values


def _queue_connection_ticket(printer):
	"""The first time a printer signs in, it prints a short ticket: the pairing is proven on paper."""
	site = frappe.utils.get_url().split("://")[-1]
	markup = "\n".join(
		[
			"[align: centre][magnify: width 2; height 2]CloudPRNT[magnify]",
			"",
			_("This printer is connected to {0}").format(site),
			f"[bold: on]{printer.label or printer.mac_address}[bold: off]",
			now_datetime().strftime("%d.%m.%Y %H:%M"),
			"[cut: feed; partial]",
		]
	)
	jobs.enqueue(printer, markup, token=f"connected-{frappe.generate_hash(length=12)}")


def _body():
	raw = frappe.request.get_data(cache=True, as_text=True) or ""
	try:
		data = json.loads(raw) if raw.strip() else {}
	except ValueError:
		data = {}
	if not isinstance(data, dict) or not data:
		data = dict(frappe.request.form or {})
	return data


def _json(payload, status=200):
	return Response(json.dumps(payload), status=status, mimetype="application/json")


def _text(message, status):
	return Response(message, status=status, mimetype="text/plain")


def _challenge():
	return Response(
		"Authentication required",
		status=401,
		mimetype="text/plain",
		headers={"WWW-Authenticate": 'Basic realm="CloudPRNT"'},
	)
