"""Text that must never reach a printer as it was typed: what a guest's QR remark or a waiter's note may hold.

Shared by the tests of the three tickets (the provisional ticket, the paid receipt, the kitchen ticket). Not a test
module: it holds no test.
"""

import re

#: Every kind of character that drives a Star printer or the reader's eye, among letters: BEL (0x07) and SUB (0x1A)
#: drive the cash drawer in Star line mode, ESC @ resets the printer, NUL, FF and TAB are controls, DEL, a C1 control
#: (NEL), the right-to-left override and isolate, a zero-width space and the byte order mark.
HOSTILE = "A\x07B\x1aC\x1b@D\x00E\x0cF\tG‮H​I﻿J\x7fK\x85L⁦M"
#: What HOSTILE prints as: the controls are gone, a tab or a form feed reads as a space.
CLEAN = "ABC@DE F GHIJKLM"

#: The C0 controls but the line feed (a markup's own line separator), DEL, the C1 controls, the Unicode line and
#: paragraph separators, the bidirectional overrides and isolates, the zero-width controls and the byte order mark.
UNSAFE = re.compile("[\x00-\x09\x0b-\x1f\x7f-\x9f​-‏  ‪-‮⁦-⁩﻿]")


def unsafe_characters(markup):
	"""The unsafe characters (as hex) in `markup`; a line break between two of its lines is the markup's own."""
	return sorted({hex(ord(char)) for line in str(markup).split("\n") for char in UNSAFE.findall(line)})
