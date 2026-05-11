from __future__ import annotations

import re

_SK_LOCAL_RE = re.compile(r"sk_local_[a-f0-9]{12}_[a-f0-9]{64}")
_BEARER_RE = re.compile(r"(?i)bearer\s+[a-z0-9._+\-/=]+", re.MULTILINE)
_JWTISH_RE = re.compile(r"\beyJ[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{10,}\b")


def redact_freeform_text(value: str, *, max_len: int | None = None) -> str:
    """Best-effort redaction for previews and logs."""
    out = value
    out = _SK_LOCAL_RE.sub("sk_local_[REDACTED]", out)
    out = _BEARER_RE.sub("Bearer [REDACTED]", out)
    out = _JWTISH_RE.sub("[REDACTED_JWT]", out)
    if max_len is not None:
        out = out[: max(0, max_len)]
    return out


def preview_from_messages(messages: list[dict[str, object]], *, max_chars: int = 100) -> str:
    parts: list[str] = []
    for m in messages:
        role = str(m.get("role", ""))
        content = m.get("content")
        if isinstance(content, str) and content.strip():
            parts.append(f"{role}: {content}")
    blob = "\n".join(parts)
    blob = redact_freeform_text(blob)
    return blob[:max_chars]
