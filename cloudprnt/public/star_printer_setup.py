#!/usr/bin/env python3
"""Configure a Star CloudPRNT printer from a computer on its network.

The ERP's CloudPRNT settings (Add a printer, or Printer credentials) give the server URL, user name and
password a printer signs in with, and this command. It finds the Star printers on the local network (or takes
--printer), signs in to the printer's web configuration with its administrator password, turns CloudPRNT on
with those three values and a short polling interval, saves, and restarts the printer. Once restarted, the
printer signs in to the ERP and prints a ticket saying it is connected.

Python 3.8 or later, no other dependency. Usage:

    python3 star_printer_setup.py --url URL --user USER --password PASSWORD [--printer 192.168.1.208]

The printer's administrator password is asked for (or read from STAR_PRINTER_ADMIN_PASSWORD); the printer's
user name is "root". Its factory password ("public") must have been changed: Star printers refuse to change
their network settings until it is.
"""

import argparse
import base64
import concurrent.futures
import getpass
import ipaddress
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ADMIN_USER = "root"
TIMEOUT = 3


def main():
	parser = argparse.ArgumentParser(description="Configure a Star CloudPRNT printer for a Neoffice site.")
	parser.add_argument("--url", required=True, help="CloudPRNT server URL given by the ERP")
	parser.add_argument("--user", required=True, help="CloudPRNT user name given by the ERP")
	parser.add_argument("--password", default=os.environ.get("CLOUDPRNT_PASSWORD"), help="CloudPRNT password given by the ERP")
	parser.add_argument("--printer", help="IP address of the printer (found on the network when omitted)")
	parser.add_argument("--poll", type=int, default=2, help="seconds between two polls of the server (default 2)")
	parser.add_argument("--no-restart", action="store_true", help="save the settings without restarting the printer")
	args = parser.parse_args()
	if not args.password:
		parser.error("--password is required")
	if not args.url.startswith("https://"):
		parser.error("--url must be the https:// address the ERP gave")

	ip = args.printer or choose(find_printers())
	admin_password = os.environ.get("STAR_PRINTER_ADMIN_PASSWORD") or getpass.getpass(
		f"Administrator password of the printer at {ip} (user root): "
	)
	printer = StarPrinter(ip, admin_password)
	page = printer.get("/html/cloudprnt.htm")
	current = form_values(page)
	print(f"Printer {ip}: CloudPRNT {current.get('cloudprnt_enable_selection', '?')}, server {current.get('server_url') or '-'}")

	printer.post(
		"/html/cloudprnt_cgi",
		{
			"cloudprnt_enable_selection": "ENABLE",
			"server_url": args.url,
			"polling_time": str(max(1, args.poll)),
			"http_response_timeout": current.get("http_response_timeout") or "60",
			"user_name": args.user,
			"cp_password": args.password,
			"Submit": "submit",
		},
	)
	applied = form_values(printer.get("/html/cloudprnt.htm"))
	if applied.get("server_url") != args.url or applied.get("polling_time") != str(max(1, args.poll)):
		sys.exit(f"The printer did not take the settings (it shows server {applied.get('server_url')!r}).")
	print(f"Printer {ip}: CloudPRNT set to {args.url}, polling every {args.poll} s, with credentials.")

	if args.no_restart:
		print("Saved without restart: the printer uses the new settings after its next restart.")
		return
	printer.post("/html/save_cgi", {"radio_sv": "r_save_res", "valid_sv": "sv_send_ok"})
	print(f"Printer {ip}: saved, restarting...")
	if wait_until_back(ip):
		print("The printer is back. Within a few seconds it signs in and prints a ticket saying it is connected.")
	else:
		print("The printer has not answered for 2 minutes: check it is on and connected, then look at the ERP.")


class StarPrinter:
	"""The web configuration of a Star printer (CenteHTTPd), signed in with HTTP Basic authentication."""

	def __init__(self, ip, password):
		self.base = f"http://{ip}"
		token = base64.b64encode(f"{ADMIN_USER}:{password}".encode()).decode()
		self.headers = {"Authorization": f"Basic {token}"}

	def get(self, path):
		return self._send(urllib.request.Request(self.base + path, headers=self.headers))

	def post(self, path, fields):
		data = urllib.parse.urlencode(fields).encode()
		headers = {**self.headers, "Content-Type": "application/x-www-form-urlencoded"}
		return self._send(urllib.request.Request(self.base + path, data=data, headers=headers, method="POST"))

	def _send(self, request):
		try:
			with urllib.request.urlopen(request, timeout=15) as response:
				return response.read().decode("utf-8", errors="replace")
		except urllib.error.HTTPError as error:
			if error.code == 401:
				sys.exit("The printer refused the administrator password.")
			sys.exit(f"The printer answered {error.code} to {request.full_url}.")
		except (urllib.error.URLError, OSError) as error:
			sys.exit(f"The printer did not answer at {request.full_url}: {error}")


def form_values(page):
	"""The current values of the CloudPRNT form of a printer's page."""
	values = {}
	for name, value in re.findall(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', page):
		values.setdefault(name, value)
	for name, block in re.findall(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', page, flags=re.S):
		selected = re.search(r'<option value="([^"]*)"[^>]*selected', block)
		if selected:
			values[name] = selected.group(1)
	return values


def find_printers():
	"""The Star printers answering on the local IPv4 network(s) of this computer."""
	networks = local_networks()
	if not networks:
		sys.exit("No local IPv4 network found: give the printer's address with --printer.")
	hosts = [str(host) for network in networks for host in network.hosts()]
	print(f"Looking for Star printers on {', '.join(map(str, networks))}...")
	with concurrent.futures.ThreadPoolExecutor(max_workers=64) as pool:
		found = [hit for hit in pool.map(probe, hosts) if hit]
	return found


def local_networks():
	networks = []
	try:
		with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe_socket:
			probe_socket.connect(("192.0.2.1", 9))  # no packet is sent: this only picks the outgoing interface
			address = probe_socket.getsockname()[0]
		networks.append(ipaddress.ip_network(f"{address}/24", strict=False))
	except OSError:
		pass
	return networks


def probe(ip):
	try:
		with urllib.request.urlopen(f"http://{ip}/", timeout=TIMEOUT) as response:
			server = response.headers.get("Server", "")
			page = response.read(20000).decode("utf-8", errors="replace")
	except Exception:
		return None
	if "CenteHTTPd" not in server and "Star Micronics" not in page:
		return None
	title = re.search(r"<title>([^<|]+)", page)
	mac = re.search(r"([0-9A-F]{2}(?::[0-9A-F]{2}){5})", page)
	return {
		"ip": ip,
		"model": (title.group(1).strip() if title else "Star printer").replace(" Network Utility", ""),
		"mac": mac.group(1) if mac else "",
	}


def choose(printers):
	if not printers:
		sys.exit("No Star printer found on the network: give its address with --printer.")
	for index, printer in enumerate(printers, 1):
		print(f"  {index}. {printer['ip']}  {printer['model']}  {printer['mac']}")
	if len(printers) == 1:
		return printers[0]["ip"]
	answer = input("Which printer? ").strip()
	try:
		return printers[int(answer) - 1]["ip"]
	except (ValueError, IndexError):
		sys.exit("No printer chosen.")


def wait_until_back(ip, seconds=120):
	time.sleep(8)
	deadline = time.time() + seconds
	while time.time() < deadline:
		try:
			with urllib.request.urlopen(f"http://{ip}/", timeout=TIMEOUT):
				return True
		except Exception:
			time.sleep(3)
	return False


if __name__ == "__main__":
	main()
