"""Repair \\b escapes that a shell heredoc turned into backspace characters (see _patch9.py)."""
from pathlib import Path

p = Path(__file__).resolve().parents[2] / "sentinelforge" / "conditions.py"
s = p.read_text(encoding="utf-8")
s = s.replace("\x08", "\\b")
p.write_text(s, encoding="utf-8")
print("remaining control chars:", sum(1 for ch in s if ch < " " and ch not in "\n\r\t"))
