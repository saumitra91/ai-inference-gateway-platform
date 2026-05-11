from __future__ import annotations

import re
from dataclasses import dataclass

_KEY_RE: Final[re.Pattern[str]] = re.compile(r"^sk_local_([a-f0-9]{12})_([a-f0-9]{64})$")


@dataclass(frozen=True, slots=True)
class ParsedAPIKey:
    public_id: str
    secret_component: str


def parse_api_key(raw: str) -> ParsedAPIKey | None:
    raw = raw.strip()
    m = _KEY_RE.match(raw)
    if not m:
        return None
    return ParsedAPIKey(public_id=m.group(1), secret_component=m.group(2))


def build_full_raw_key(parsed: ParsedAPIKey) -> str:
    return f"sk_local_{parsed.public_id}_{parsed.secret_component}"
