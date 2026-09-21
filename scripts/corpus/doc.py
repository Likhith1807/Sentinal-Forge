"""A text builder that records character-offset spans as text is appended.

Spans are captured at write time, so an offset can never disagree with the text it labels.
Templates use ``{slot}`` placeholders; a slot value is either a plain string or a ``Tagged``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_SLOT = re.compile(r"\{(\w+)\}")


@dataclass(frozen=True)
class Tagged:
    text: str
    label: str
    meta: tuple = ()          # ((key, value), ...) copied into the span record

    @classmethod
    def of(cls, text: str, label: str, **meta) -> "Tagged":
        return cls(text, label, tuple(sorted(meta.items())))


@dataclass
class Doc:
    parts: list = field(default_factory=list)
    spans: list = field(default_factory=list)
    pos: int = 0

    def add(self, text: str) -> "Doc":
        self.parts.append(text)
        self.pos += len(text)
        return self

    def tag(self, item: Tagged) -> "Doc":
        start = self.pos
        self.add(item.text)
        self.spans.append({"label": item.label, "text": item.text, "start": start, "end": self.pos, **dict(item.meta)})
        return self

    def fill(self, template: str, **slots) -> "Doc":
        """Append ``template`` with every ``{slot}`` replaced (tagged slots are recorded as spans)."""
        cursor = 0
        for match in _SLOT.finditer(template):
            self.add(template[cursor:match.start()])
            value = slots[match.group(1)]
            for segment in (value if isinstance(value, (list, tuple)) else [value]):
                self.tag(segment) if isinstance(segment, Tagged) else self.add(str(segment))
            cursor = match.end()
        self.add(template[cursor:])
        return self

    def text(self) -> str:
        return "".join(self.parts)

    def verify(self) -> None:
        """Every recorded span must equal the text at its offsets."""
        body = self.text()
        for span in self.spans:
            if body[span["start"]:span["end"]] != span["text"]:
                raise AssertionError(f"span mismatch: {span} vs {body[span['start']:span['end']]!r}")
