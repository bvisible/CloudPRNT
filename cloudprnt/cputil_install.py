"""Star's cputil comes from Star at install and at every migrate: this public repository does not ship it.

Star's licence for its prebuilt cputil does not let us redistribute the binary, so it left the tree on 10.11.2026
(bvisible/neoffice-maintenance#2096). It is downloaded from Star's own link, the one Star's CloudPRNT manual gives
under "Pre-built Binaries", Linux x64 (https://www.star-m.jp/prjump/000199.html, which redirects to the versioned
archive below), and pinned by sha256: the archive, and the binary inside it. Version 2.0.1 is kept on purpose: it is
the build render.py and receipt_markup.py were measured against, byte for byte the file the tree carried before.

A file already in place with the pinned sha256 is kept: a server that has it never downloads it again. A download
that fails or does not match changes nothing and is logged; render.py then offers the formats the printer renders
itself (cputil_available() is false), so printing goes on, without Star's converter.
"""

import hashlib
import io
import os
import tarfile
import tempfile
import urllib.request

import frappe

from cloudprnt.render import CPUTIL

URL = "https://www.star-m.jp/products/s_print/sdk/StarDocumentMarkup/cputil/cputil-linux-x64_v201.tar.gz"
ARCHIVE_SHA256 = "f1f1d4e610333213b95e93140bc39ebd598395acd01b398696ac11d487308917"
BINARY_SHA256 = "b15ad6d447816848443646673fa989d73761ae45bf2052f84ae1a2a7c629825a"
MEMBER = "cputil-linux-x64_v201/cputil-linux-x64/cputil"

TIMEOUT = 120
#: The archive is about 21 MB; anything far larger is not Star's archive.
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024


def sha256_of_file(path):
	digest = hashlib.sha256()
	with open(path, "rb") as handle:
		for block in iter(lambda: handle.read(1 << 20), b""):
			digest.update(block)
	return digest.hexdigest()


def download(url=URL):
	"""The archive's bytes, or an exception: no shell, a time limit and a size limit."""
	with urllib.request.urlopen(url, timeout=TIMEOUT) as response:  # noqa: S310 (a fixed https URL)
		data = response.read(MAX_ARCHIVE_BYTES + 1)
	if len(data) > MAX_ARCHIVE_BYTES:
		raise ValueError(f"the archive is larger than {MAX_ARCHIVE_BYTES} bytes")
	return data


def binary_from_archive(data):
	"""The cputil binary inside Star's archive, checked against both pins."""
	if hashlib.sha256(data).hexdigest() != ARCHIVE_SHA256:
		raise ValueError("the archive does not match the pinned sha256")
	with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
		member = archive.getmember(MEMBER)
		if not member.isfile():
			raise ValueError(f"{MEMBER} is not a file in the archive")
		binary = archive.extractfile(member).read()
	if hashlib.sha256(binary).hexdigest() != BINARY_SHA256:
		raise ValueError("the binary does not match the pinned sha256")
	return binary


def ensure_cputil(target=CPUTIL, fetch=download):
	"""Put Star's cputil at `target` when it is missing or not the pinned build.

	Returns "present" (already the pinned build, nothing fetched), "installed", or "failed: <reason>" (nothing
	changed, the reason logged)."""
	if os.path.isfile(target) and sha256_of_file(target) == BINARY_SHA256:
		if not os.access(target, os.X_OK):
			os.chmod(target, 0o755)
		return "present"
	try:
		binary = binary_from_archive(fetch())
		folder = os.path.dirname(target)
		os.makedirs(folder, exist_ok=True)
		handle, partial = tempfile.mkstemp(prefix=".cputil.", dir=folder)
		try:
			with os.fdopen(handle, "wb") as out:
				out.write(binary)
			os.chmod(partial, 0o755)
			os.replace(partial, target)
		finally:
			if os.path.exists(partial):
				os.unlink(partial)
	except Exception as error:
		frappe.log_error("CloudPRNT: Star's cputil could not be installed", f"{URL}\n{error!r}")
		return f"failed: {error}"
	return "installed"


def install():
	"""after_install and after_migrate: an update that removed the binary from the tree puts Star's back."""
	ensure_cputil()
