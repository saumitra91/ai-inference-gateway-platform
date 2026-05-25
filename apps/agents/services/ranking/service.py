from __future__ import annotations

import logging
import re
from typing import Any

from apps.agents.services.llm.service import LLMService
from apps.agents.services.sources.base import SourceItem

logger = logging.getLogger(__name__)


class RelevanceRanker:
    def __init__(self, llm_service: LLMService | None = None):
        self._llm = llm_service or LLMService()

    def rank(self, items: list[SourceItem], instructions: str) -> list[tuple[SourceItem, float, str]]:
        if not items:
            return []

        scored: list[tuple[SourceItem, float, str]] = []

        for item in items:
            score, explanation = self._score_item(item, instructions)
            scored.append((item, score, explanation))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    def _score_item(self, item: SourceItem, instructions: str) -> tuple[float, str]:
        text = f"{item.title}\n{item.content[:500]}".lower()
        instruction_lower = instructions.lower()

        keyword_score = 0.0
        keywords = re.findall(r'\b(\w+)\b', instruction_lower)
        keywords = [k for k in keywords if len(k) > 3]

        if keywords:
            matches = sum(1 for k in keywords if k in text)
            keyword_score = min(matches / len(keywords), 1.0)

        try:
            explanation = self._llm.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "Score the relevance of this item (1-10) based on the user's instructions. "
                            "Return only: SCORE: <number> EXPLANATION: <one sentence>"
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Instructions: {instructions}\n\nTitle: {item.title}\nContent: {item.content[:800]}"
                        ),
                    },
                ],
                max_tokens=128,
                temperature=0.2,
            )
            score_match = re.search(r'SCORE:\s*(\d+(?:\.\d+)?)', explanation)
            llm_score = float(score_match.group(1)) / 10.0 if score_match else keyword_score
            explanation_text = explanation.split("EXPLANATION:")[-1].strip() if "EXPLANATION:" in explanation else ""
        except Exception as exc:
            logger.warning("LLM scoring failed for '%s': %s", item.title, exc)
            llm_score = keyword_score
            explanation_text = ""

        final_score = (llm_score * 0.7) + (keyword_score * 0.3)
        return round(final_score, 2), explanation_text

    def rank_batch(self, items: list[SourceItem], instructions: str, max_items: int = 50) -> list[tuple[SourceItem, float, str]]:
        if not items:
            return []

        ranked = self.rank(items[:max_items], instructions)

        ranked = [(item, score, expl) for item, score, expl in ranked if score >= 0.3]

        return ranked
