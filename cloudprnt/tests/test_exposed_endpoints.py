"""
What the app exposes over HTTP, read from its source (no site needed).

On 2026-10-08 a reported flaw showed that a whitelisted method built a shell command line from the caller's
arguments and ran it with `shell=True`: any signed-in account, a portal customer included, could run commands
on the server. That method, the anonymous order-history receiver and the anonymous debug logger had no caller
anywhere and were removed. These tests keep it that way:

* no code of the app runs a shell (`shell=True`, `os.system`, `os.popen`);
* the removed methods stay gone;
* what an anonymous visitor may call is the CloudPRNT protocol a Star printer speaks without a Frappe session,
  and nothing else: a new `allow_guest` method fails here until it has been reviewed and added below.
"""

import ast
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]

#: The CloudPRNT protocol: a Star printer polls, fetches and deletes its jobs without a Frappe session.
GUEST_METHODS = {
	"cloudprnt_server.cloudprnt_poll",
	"cloudprnt_server.cloudprnt_job",
	"cloudprnt_server.cloudprnt_delete",
}

REMOVED = {
	"print_job.call_execute_cputil",
	"print_job.process_order_history_from_php",
	"cloudprnt_server.cloudprnt_debug",
}

SHELL_CALLS = {("os", "system"), ("os", "popen")}


def _sources():
	for path in sorted(APP.rglob("*.py")):
		if "tests" in path.relative_to(APP).parts:
			continue
		yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _dotted(path):
	return ".".join(path.relative_to(APP).with_suffix("").parts)


def _whitelist(decorator):
	"""(is_whitelisted, allow_guest) for one decorator node."""
	target = decorator.func if isinstance(decorator, ast.Call) else decorator
	name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", None)
	if name != "whitelist":
		return False, False
	guest = isinstance(decorator, ast.Call) and any(
		k.arg == "allow_guest" and isinstance(k.value, ast.Constant) and k.value.value is True
		for k in decorator.keywords
	)
	return True, guest


def _methods():
	"""{module.function: allow_guest} for every whitelisted function of the app."""
	found = {}
	for path, tree in _sources():
		for node in ast.walk(tree):
			if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
				for decorator in node.decorator_list:
					whitelisted, guest = _whitelist(decorator)
					if whitelisted:
						found[f"{_dotted(path)}.{node.name}"] = guest
	return found


class TestExposedEndpoints(unittest.TestCase):
	def test_no_code_of_the_app_runs_a_shell(self):
		offenders = []
		for path, tree in _sources():
			for node in ast.walk(tree):
				if not isinstance(node, ast.Call):
					continue
				func = node.func
				if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
					if (func.value.id, func.attr) in SHELL_CALLS:
						offenders.append(f"{path.relative_to(APP)}:{node.lineno} {func.value.id}.{func.attr}")
				for keyword in node.keywords:
					if keyword.arg == "shell" and not (
						isinstance(keyword.value, ast.Constant) and keyword.value.value is False
					):
						offenders.append(f"{path.relative_to(APP)}:{node.lineno} shell=")
		self.assertEqual(offenders, [], "a command line run by a shell lets the caller's text become a command")

	def test_the_removed_methods_stay_gone(self):
		names = set()
		for path, tree in _sources():
			for node in ast.walk(tree):
				if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
					names.add(f"{_dotted(path)}.{node.name}")
		self.assertEqual(REMOVED & names, set())

	def test_an_anonymous_visitor_reaches_the_printer_protocol_only(self):
		guest = {name for name, allow_guest in _methods().items() if allow_guest}
		self.assertEqual(guest, GUEST_METHODS)

	def test_the_reading_finds_the_methods_it_judges(self):
		"""Guards the three tests above against reading nothing (a moved folder, a renamed decorator)."""
		methods = _methods()
		self.assertGreater(len(methods), 10, methods)
		self.assertIn("api.print_pos_invoice", methods)


if __name__ == "__main__":
	unittest.main()
