from __future__ import annotations

from apps.inference.schemas import ChatCompletionRequest


def prompt_char_length(req: ChatCompletionRequest) -> int:
    total = 0
    for m in req.messages:
        if m.content:
            total += len(m.content)
    return total


def rough_token_estimate_from_chars(chars: int) -> int:
    # Cheap heuristic for CPU inference platforms; replace with tokenizer hooks later.
    return max(1, int(chars // 4))
