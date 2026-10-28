"""Instant printing (broker.py): who receives a broker account, and when a printer is signalled.

What must hold:
* a paired printer that asks for cloudprnt-setting.json is challenged (401) whether instant printing is on or not, and
  once signed in it is noted as able to print instantly; an anonymous caller changes nothing;
* with instant printing off, or without the platform's credential, every printer is told "HTTP only" (404);
* with it on, only the printer's own account receives the broker, its account and its password; the password is kept
  encrypted, never in the row;
* a job queued for a printer on the broker is signalled once committed, and only then; a printer without an account,
  or a site with instant printing off, is not;
* the platform is called with this site's credential and nothing else that identifies it.
"""

import json
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils.password import remove_encrypted_password
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from cloudprnt import broker
from cloudprnt import print_queue_manager as jobs
from cloudprnt.cloudprnt.doctype.cloudprnt_settings.cloudprnt_settings import pair_printer
from cloudprnt.printers import get_printer, update_printer
from cloudprnt.protocol import server_setting

MAC = "00:11:62:AA:00:41"
REGISTERED = {
	"account": "instance-a-001162aa0041",
	"password": "a-test-broker-password",
	"host": "broker.example.org",
	"port": 8883,
	"topic": "star/cloudprnt/to-device/instance-a-001162aa0041",
	"poll_seconds": 30,
	"reload_minutes": 60,
}


def setting_request(user="Guest", mac=MAC):
	builder = EnvironBuilder(
		method="GET",
		path="/cloudprnt-setting.json",
		query_string={"mac": mac.lower(), "replaced_path": "cloudprnt"},
		environ_base={"REMOTE_ADDR": "192.0.2.10"},
	)
	frappe.local.request = Request(builder.get_environ())
	frappe.local.request_ip = "192.0.2.10"
	frappe.set_user(user)
	try:
		response = server_setting()
	finally:
		frappe.set_user("Administrator")
	body = response.get_data(as_text=True)
	return response.status_code, (json.loads(body) if response.mimetype == "application/json" else body)


class TestInstantPrinting(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.pair = pair_printer(label="_Test CloudPRNT instant")
		cls.user = get_printer(cls.pair["printer"]).device_user
		update_printer(cls.pair["printer"], {"mac_address": MAC})
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		from cloudprnt.tests.test_drawer import _remove_rows

		frappe.db.set_single_value("CloudPRNT Settings", "instant_printing", 0)
		_remove_rows([cls.pair["printer"]])
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		update_printer(self.pair["printer"], {"instant_capable": 0, "broker_account": None, "broker_connection": None})
		remove_encrypted_password(broker.PRINTER, self.pair["printer"], broker.PASSWORD_FIELD)
		frappe.db.set_single_value("CloudPRNT Settings", "instant_printing", 0)

	def switch_on(self):
		frappe.db.set_single_value("CloudPRNT Settings", "instant_printing", 1)
		patcher = patch.object(broker, "available", return_value=True)
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_a_printer_that_asks_can_print_instantly_and_hears_http_only_while_it_is_off(self):
		status, _ = setting_request("Guest")
		self.assertEqual(status, 401, "challenged, as its polls are, whatever the setting")
		self.assertFalse(get_printer(self.pair["printer"]).instant_capable, "an anonymous caller writes nothing")
		status, _ = setting_request(self.user)
		self.assertEqual(status, 404)
		self.assertTrue(get_printer(self.pair["printer"]).instant_capable)

	def test_an_unknown_printer_hears_http_only(self):
		status, _ = setting_request("Guest", mac="00:11:62:FF:FF:FF")
		self.assertEqual(status, 404)

	def test_without_the_platforms_credential_nothing_changes(self):
		frappe.db.set_single_value("CloudPRNT Settings", "instant_printing", 1)
		with patch.object(broker, "_hub") as hub:
			status, _ = setting_request(self.user)
		self.assertEqual(status, 404)
		hub.assert_not_called()

	def test_an_anonymous_request_is_challenged_and_learns_nothing(self):
		self.switch_on()
		with patch.object(broker, "_hub") as hub:
			status, body = setting_request("Guest")
		self.assertEqual(status, 401)
		self.assertNotIn("password", str(body))
		hub.assert_not_called()

	def test_the_printers_own_account_receives_the_broker_and_its_password_kept_encrypted(self):
		self.switch_on()
		with patch.object(broker, "_hub", return_value=dict(REGISTERED)) as hub:
			status, setting = setting_request(self.user)
			again, _ = setting_request(self.user)
		self.assertEqual((status, again), (200, 200))
		hub.assert_called_once_with("register", MAC)
		mqtt = setting["settingForMQTT"]
		self.assertEqual(setting["serverSupportProtocol"], ["HTTP", "MQTT"])
		self.assertTrue(mqtt["useTriggerPOST"])
		connection = mqtt["mqttConnectionSetting"]
		self.assertEqual((connection["hostName"], connection["portNumber"], connection["useTls"]), ("broker.example.org", 8883, True))
		self.assertEqual(connection["deviceSubscribeTopicName"], REGISTERED["topic"])
		self.assertEqual(connection["authenticationSetting"], {"username": REGISTERED["account"], "password": REGISTERED["password"]})
		row = frappe.db.get_value(broker.PRINTER, self.pair["printer"], ["broker_account", "broker_connection"], as_dict=True)
		self.assertEqual(row.broker_account, REGISTERED["account"])
		self.assertNotIn(REGISTERED["password"], row.broker_connection or "", "the password is never in the row")

	def test_another_printers_account_gets_http_only(self):
		self.switch_on()
		other = pair_printer(label="_Test CloudPRNT instant other")
		try:
			other_user = get_printer(other["printer"]).device_user
			update_printer(other["printer"], {"mac_address": "00:11:62:AA:00:42"})
			with patch.object(broker, "_hub", return_value=dict(REGISTERED)) as hub:
				status, body = setting_request(other_user, mac=MAC)
			self.assertEqual(status, 404)
			self.assertNotIn("password", str(body))
			hub.assert_not_called()
		finally:
			from cloudprnt.tests.test_drawer import _remove_rows

			_remove_rows([other["printer"]])

	def test_a_queued_job_is_signalled_after_commit_only_for_a_printer_on_the_broker(self):
		row = get_printer(self.pair["printer"])
		with patch.object(broker.frappe, "enqueue") as enqueue:
			jobs.enqueue(row, "Receipt")
			enqueue.assert_not_called()  # no account, instant printing off
			self.switch_on()
			update_printer(self.pair["printer"], {"broker_account": REGISTERED["account"]})
			jobs.enqueue(get_printer(self.pair["printer"]), "Receipt")
		enqueue.assert_called_once()
		self.assertEqual(enqueue.call_args.args[0], "cloudprnt.broker.trigger_now")
		self.assertTrue(enqueue.call_args.kwargs["enqueue_after_commit"])
		self.assertEqual(enqueue.call_args.kwargs["mac"], MAC)
		frappe.db.delete(jobs.DOCTYPE, {"printer": self.pair["printer"]})

	def test_the_platform_is_never_called_in_clear(self):
		with patch("requests.post") as post, patch.dict(
			frappe.conf, {broker.CREDENTIAL_CONF: "INSTANCE-A:abc", "cloudprnt_hub_url": "http://hub.example.org"}
		):
			with self.assertRaises(ValueError):
				broker._hub("trigger", MAC)
		post.assert_not_called()

	def test_the_platform_hears_this_sites_credential_and_the_printer_only(self):
		response = MagicMock()
		response.json.return_value = {"message": {"signalled": True}}
		with patch("requests.post", return_value=response) as post, patch.dict(
			frappe.conf, {broker.CREDENTIAL_CONF: "INSTANCE-A:abc", "cloudprnt_hub_url": "https://hub.example.org/"}
		):
			broker.trigger_now(MAC)
		url = post.call_args.args[0]
		self.assertEqual(url, "https://hub.example.org/api/method/neoffice_devops.api.cloudprnt_broker.trigger")
		self.assertEqual(post.call_args.kwargs["data"], {"mac": MAC})
		self.assertEqual(post.call_args.kwargs["headers"], {"X-Instance-Credential": "INSTANCE-A:abc"})

	def test_a_signal_that_fails_is_logged_and_never_raised(self):
		with patch.object(broker, "_hub", side_effect=ConnectionError("down")), patch.object(broker.frappe, "log_error") as log:
			broker.trigger_now(MAC)
		log.assert_called_once()
