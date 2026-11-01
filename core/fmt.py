from __future__ import annotations


def money(x: float | None, cur: str = "₹", style: str = "indian") -> str:
    if x is None:
        return "—"
    neg = x < 0
    n = int(round(abs(x)))
    s = str(n)
    if style == "indian" and len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:]); head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    else:
        s = f"{n:,}"
    return f"{'-' if neg else ''}{cur}{s}"
