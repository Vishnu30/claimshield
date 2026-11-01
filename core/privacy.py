"""PII vault: redact identifiers before any text leaves the machine, restore them locally afterwards."""
from __future__ import annotations
import re

PATTERNS = [
    ("AADHAAR", re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b")),
    ("PAN", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")),
    ("PHONE", re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
]
NAME_LINE = re.compile(r"(?im)^(\s*(?:patient(?:'s)?\s*name|name of (?:the )?patient|insured name|proposer)\s*[:\-]\s*)(.+)$")


class Vault:
    def __init__(self) -> None:
        self.map: dict[str, str] = {}
        self.rev: dict[str, str] = {}

    def _token(self, kind: str, value: str) -> str:
        if value in self.rev:
            return self.rev[value]
        t = f"[{kind}_{len(self.map) + 1}]"
        self.map[t] = value
        self.rev[value] = t
        return t

    def redact(self, text: str) -> str:
        if not text:
            return text
        text = NAME_LINE.sub(lambda m: m.group(1) + self._token("NAME", m.group(2).strip()), text)
        for kind, rx in PATTERNS:
            text = rx.sub(lambda m, k=kind: self._token(k, m.group(0)), text)
        return text

    def restore(self, text: str) -> str:
        for t, v in self.map.items():
            text = text.replace(t, v)
        return text

    @property
    def count(self) -> int:
        return len(self.map)
