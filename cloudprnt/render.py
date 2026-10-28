"""Turn a print job into what a Star printer prints, with the converter Star publishes for CloudPRNT servers.

A job is kept as Star Document Markup. When the printer fetches it, it names the format it wants
(StarPRNT commands, Star Line commands, ...), and cputil (bin/cputil, Star's v2.0.1 build for linux x86-64)
renders the markup into that format for the printer's paper: text in UTF-8, magnified and bold text, columns,
barcodes, QR codes, images, the cut, the cash drawer and the buzzer.

cputil runs with a fixed argument list and no shell. The markup goes through a temporary file, never on the
command line; the media type is one of MARKUP_OUTPUTS, never the request's text as given; the ASB status is
checked to be hex before it is passed on. On 2026-10-08 a whitelisted method that ran cputil through a shell
with the caller's arguments let any signed-in account run commands on the server: it was removed, and
tests/test_exposed_endpoints.py keeps any shell out of the app.
"""

import json
import os
import re
import subprocess
import tempfile

CPUTIL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", "cputil")

#: What cputil renders markup into, in the order a printer is offered them: it takes the first it knows.
MARKUP_OUTPUTS = (
	"application/vnd.star.starprnt",
	"application/vnd.star.line",
	"application/vnd.star.starprntcore",
	"application/vnd.star.linematrix",
)

#: What a printer renders itself. Offered after MARKUP_OUTPUTS, and alone when cputil cannot run.
PRINTER_RENDERED = ("text/vnd.star.markup", "text/plain")

#: cputil's paper presets, by the paper width in millimetres.
PAPER_PRESETS = {58: "thermal58", 80: "thermal80", 82: "thermal82", 83: "thermal83", 112: "thermal112"}

DRAWER_OPTIONS = {"start": "drawer-start", "end": "drawer-end"}

TIMEOUT = 30

_ASB = re.compile(r"^[0-9A-Fa-f]{1,2}( [0-9A-Fa-f]{1,2}){0,63}$")
_TAG = re.compile(r"\[[^\]]*\]")


class RenderError(Exception):
	"""cputil could not produce the job."""


def cputil_available():
	return os.path.isfile(CPUTIL) and os.access(CPUTIL, os.X_OK)


def offered_media_types(printer_encodings=None):
	"""The formats a markup job is offered in: what can be produced here, narrowed to what the printer said it
	takes when it said so (the CloudPRNT "Encodings" request)."""
	ours = (list(MARKUP_OUTPUTS) if cputil_available() else []) + list(PRINTER_RENDERED)
	known = [media_type for media_type in ours if media_type in (printer_encodings or ())]
	return known or ours


def paper_option(paper_width=None, print_dots=None):
	"""cputil's device constraint: the exact printable width in dots when the printer reported it, else the
	preset nearest to its paper width, else 80 mm."""
	try:
		dots = int(print_dots or 0)
	except (TypeError, ValueError):
		dots = 0
	if dots > 0:
		return ["printarea", str(dots)]
	try:
		width = round(float(str(paper_width or "").replace("mm", "").strip() or 0))
	except ValueError:
		width = 0
	if not width:
		return [PAPER_PRESETS[80]]
	return [PAPER_PRESETS[min(PAPER_PRESETS, key=lambda preset: abs(preset - width))]]


def render(markup, media_type, paper_width=None, print_dots=None, drawer=None, buzzer=0):
	"""The bytes of a markup job in `media_type`.

	drawer: "start" or "end" opens the cash drawer wired to the printer at that end of the job; a job whose
	markup is empty and that opens the drawer feeds no paper.
	buzzer: number of rings at the end of the job, when a buzzer is wired.
	"""
	markup = markup or ""
	if media_type == "text/vnd.star.markup":
		return markup.encode("utf-8")
	if media_type == "text/plain":
		return plain_text(markup).encode("utf-8")
	if media_type not in MARKUP_OUTPUTS:
		raise RenderError(f"Media type not rendered here: {media_type}")
	if not cputil_available():
		raise RenderError(f"cputil is missing or not executable: {CPUTIL}")

	args = [CPUTIL, *paper_option(paper_width, print_dots), "scale-to-fit", "dither"]
	args.append(DRAWER_OPTIONS.get(drawer or "", "drawer-none"))
	rings = _int(buzzer)
	if rings > 0:
		args += ["buzzer-end", str(min(rings, 9))]

	with tempfile.TemporaryDirectory(prefix="cloudprnt-") as folder:
		source = os.path.join(folder, "job.stm")
		with open(source, "w", encoding="utf-8") as handle:
			handle.write(markup)
		args += ["decode", media_type, source, "-"]
		try:
			result = subprocess.run(args, capture_output=True, timeout=TIMEOUT, cwd=folder, env=_env())
		except (OSError, subprocess.TimeoutExpired) as error:
			raise RenderError(f"cputil did not run: {error}") from error

	if result.returncode != 0 or not result.stdout:
		detail = (result.stderr or b"").decode("utf-8", errors="replace").strip().splitlines()
		raise RenderError(f"cputil exited with {result.returncode}: {detail[-1] if detail else 'no output'}")
	return result.stdout


def decode_status(asb):
	"""The printer's ASB status (hex bytes, as CloudPRNT reports them) as cputil's JSON, or None."""
	asb = (asb or "").strip()
	if not asb or not _ASB.match(asb) or not cputil_available():
		return None
	try:
		result = subprocess.run(
			[CPUTIL, "jsonstatus", asb], capture_output=True, timeout=TIMEOUT, env=_env()
		)
		return json.loads(result.stdout) if result.returncode == 0 else None
	except (OSError, subprocess.TimeoutExpired, ValueError):
		return None


def plain_text(markup):
	"""The text of a markup job, its tags dropped: what a printer that renders nothing else prints."""
	lines = [_TAG.sub("", line).rstrip() for line in (markup or "").splitlines()]
	return "\n".join(lines).strip("\n") + "\n"


def _env():
	env = dict(os.environ)
	# cputil is a .NET program: without ICU on a server it stops at start-up unless told not to look for it.
	env["DOTNET_SYSTEM_GLOBALIZATION_INVARIANT"] = "1"
	env["DOTNET_CLI_TELEMETRY_OPTOUT"] = "1"
	return env


def _int(value):
	try:
		return int(value or 0)
	except (TypeError, ValueError):
		return 0
