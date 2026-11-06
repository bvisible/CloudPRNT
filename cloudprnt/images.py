"""The images a job prints, made files on this server before cputil renders the job.

cputil downloads the image of an `[image: url http…]` tag itself, from the server, and follows redirects (as a
test server showed on 06.11): a link typed in a test print, or in the receipt's logo settings, made the server
request any address it reaches, its own internal services included. Every image of a job is now a file by the
time cputil reads it:

* a web image is downloaded here, from an address of the public Internet only. The connection is made to the very
  address that was checked (no second lookup a DNS answer could change), each redirect is checked again, at most
  MAX_REDIRECTS of them, and the answer must be an image of at most MAX_BYTES;
* a file of the site itself (/files/…) is read from the site's folder, with no request;
* a file this app made (the company logo flattened for paper, receipt_markup.company_logo_path) stays as it is.

Any other image is left out of the job, which still prints, and the reason goes to the Error Log once an hour.
A printer that renders the markup itself (text/vnd.star.markup) fetches its images from its own network: this
module is not on that path.
"""

import hashlib
import io
import ipaddress
import os
import re
import socket
import time
from urllib.parse import unquote, urljoin, urlsplit

import frappe

MAX_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3
CONNECT_SECONDS = 5
READ_SECONDS = 10
#: The whole download of one image: cputil's own limit (render.TIMEOUT) comes after it.
TOTAL_SECONDS = 15
_REDIRECTS = (301, 302, 303, 307, 308)
_TAG = re.compile(r"\[image:([^\]]*)\]", re.IGNORECASE)
_URL = re.compile(r"^\s*url\s+(.*?)\s*$", re.IGNORECASE)
#: What a web address written into a markup tag never holds: it would close the tag or start another one.
_NOT_IN_ADDRESS = re.compile(r"[\s\[\];]")
_FORMATS = {"PNG": "png", "JPEG": "jpg", "GIF": "gif", "BMP": "bmp"}


class Refused(Exception):
	"""The image is not printed: the reason says why."""


def is_web_address(value):
	"""Whether `value` is an http(s) address that a markup tag can carry as it is."""
	value = (value or "").strip()
	return value.lower().startswith(("http://", "https://")) and not _NOT_IN_ADDRESS.search(value)


def localise(markup, folder):
	"""`markup` with each image tag's url made a file in `folder` (or one this app made), or the tag left out."""

	def replace(match):
		params = match.group(1).split(";")
		for index, param in enumerate(params):
			found = _URL.match(param)
			if not found:
				continue
			url = found.group(1)
			try:
				params[index] = f" url {_file_for(url, folder)}"
			except Refused as error:
				_note_refused(url, str(error))
				return ""
			return "[image:" + ";".join(params) + "]"
		return ""  # an image tag without a url prints nothing anyway

	return _TAG.sub(replace, markup or "")


def _file_for(url, folder):
	if url.lower().startswith(("http://", "https://")):
		return _own_file(url) or download(url, folder)
	path = os.path.realpath(url)
	for base in (_made_here(), os.path.realpath(folder)):
		if path.startswith(base + os.sep) and os.path.isfile(path):
			return path
	raise Refused("not a web address nor a file made for printing")


def _made_here():
	"""The folder where this app keeps the images it prepares for paper (receipt_markup.company_logo_path)."""
	return os.path.realpath(frappe.get_site_path("private", "cloudprnt"))


def _own_file(url):
	"""The public file of this site that `url` names, read from its folder, or None."""
	parts = urlsplit(url)
	own = {urlsplit(frappe.utils.get_url()).hostname, getattr(frappe.local, "site", None)}
	if not parts.hostname or parts.hostname.lower() not in {host.lower() for host in own if host}:
		return None
	if not parts.path.startswith("/files/"):
		return None
	base = os.path.realpath(frappe.get_site_path("public", "files"))
	path = os.path.realpath(os.path.join(base, unquote(parts.path[len("/files/") :])))
	if path.startswith(base + os.sep) and os.path.isfile(path):
		return path
	return None


def download(url, folder):
	"""Download the image at `url` into `folder` and return its path. Raises Refused."""
	for _hop in range(MAX_REDIRECTS + 1):
		parts = urlsplit(url)
		if parts.scheme not in ("http", "https") or not parts.hostname:
			raise Refused("not a web address")
		try:
			port = parts.port or (443 if parts.scheme == "https" else 80)
		except ValueError as error:
			raise Refused("not a web address") from error
		address = public_address(parts.hostname, port)
		pool, response = _get(parts, address, port)
		try:
			if response.status in _REDIRECTS:
				location = response.headers.get("Location")
				if not location:
					raise Refused(f"HTTP {response.status} without a location")
				url = urljoin(url, location)
				continue
			if response.status != 200:
				raise Refused(f"HTTP {response.status}")
			data = _read(response)
		finally:
			response.release_conn()
			pool.close()
		return _save(data, folder)
	raise Refused("too many redirects")


def public_address(host, port):
	"""The address `host` is reached at, when every address it has is on the public Internet. Raises Refused."""
	try:
		infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
	except (OSError, UnicodeError) as error:
		raise Refused(f"{host} has no address") from error
	addresses = []
	for info in infos:
		address = ipaddress.ip_address(info[4][0].split("%")[0])
		if address.version == 6 and address.ipv4_mapped:
			address = address.ipv4_mapped
		if not address.is_global or address.is_multicast:
			raise Refused(f"{host} is not on the public Internet")
		addresses.append(str(address))
	if not addresses:
		raise Refused(f"{host} has no address")
	# IPv4 first: a server without an IPv6 route still reaches a host that has both.
	return sorted(addresses, key=lambda address: ":" in address)[0]


def _get(parts, address, port):
	import certifi
	import urllib3

	host_header = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
	if parts.port:
		host_header += f":{parts.port}"
	options = {"timeout": urllib3.Timeout(connect=CONNECT_SECONDS, read=READ_SECONDS), "retries": False, "maxsize": 1}
	if parts.scheme == "https":
		pool = urllib3.HTTPSConnectionPool(
			address,
			port,
			server_hostname=parts.hostname,
			assert_hostname=parts.hostname,
			cert_reqs="CERT_REQUIRED",
			ca_certs=certifi.where(),
			**options,
		)
	else:
		pool = urllib3.HTTPConnectionPool(address, port, **options)
	path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
	try:
		response = pool.urlopen(
			"GET",
			path,
			headers={"Host": host_header, "User-Agent": "CloudPRNT receipt renderer", "Accept": "image/*"},
			redirect=False,
			preload_content=False,
			retries=False,
		)
	except urllib3.exceptions.HTTPError as error:
		pool.close()
		raise Refused(f"{parts.hostname} did not answer: {error.__class__.__name__}") from error
	return pool, response


def _read(response):
	import urllib3

	length = response.headers.get("Content-Length")
	if length and length.isdigit() and int(length) > MAX_BYTES:
		raise Refused("larger than an image a receipt prints")
	deadline = time.monotonic() + TOTAL_SECONDS
	data = bytearray()
	try:
		for chunk in response.stream(64 * 1024):
			data += chunk
			if len(data) > MAX_BYTES:
				raise Refused("larger than an image a receipt prints")
			if time.monotonic() > deadline:
				raise Refused("too slow to download")
	except urllib3.exceptions.HTTPError as error:
		raise Refused(f"the download broke off: {error.__class__.__name__}") from error
	return bytes(data)


def _save(data, folder):
	from PIL import Image

	try:
		with Image.open(io.BytesIO(data)) as image:
			kind = _FORMATS.get(image.format)
			image.verify()
	except Exception as error:
		raise Refused("not an image") from error
	if not kind:
		raise Refused("not an image a receipt prints")
	path = os.path.join(folder, f"web-{hashlib.sha1(data).hexdigest()[:16]}.{kind}")
	with open(path, "wb") as handle:
		handle.write(data)
	return path


def _note_refused(url, reason):
	key = f"cloudprnt_image_refused_{hashlib.sha1(url.encode('utf-8', 'replace')).hexdigest()[:16]}"
	try:
		if frappe.cache().get_value(key):
			return
		frappe.cache().set_value(key, 1, expires_in_sec=3600)
		frappe.log_error("CloudPRNT: an image of a job was left out", f"{url[:300]}\n{reason}")
	except Exception:
		pass
