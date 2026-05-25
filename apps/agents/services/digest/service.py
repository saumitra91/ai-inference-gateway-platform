from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from apps.agents.models import Agent, AgentRun, AgentResult
from apps.agents.services.llm.service import LLMService

logger = logging.getLogger(__name__)


class DigestAssembler:
    def __init__(self, llm_service: LLMService | None = None):
        self._llm = llm_service or LLMService()

    def assemble(
        self,
        agent: Agent,
        run: AgentRun,
        results: list[AgentResult],
    ) -> str:
        if not results:
            return self._empty_digest(agent)

        summary = run.summary or "No summary available."

        digest_parts: list[str] = []
        digest_parts.append(f"*{agent.name}* — {agent.get_type_display()}")
        digest_parts.append(f"`{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}`")
        digest_parts.append("")

        digest_parts.append(f"*Summary:* {summary}")
        digest_parts.append("")

        top_results = sorted(results, key=lambda r: r.match_score or 0, reverse=True)[:15]
        digest_parts.append(f"*Top Results ({len(top_results)} of {len(results)}):*")

        for i, result in enumerate(top_results, 1):
            title = (result.title or "Untitled")[:100]
            score = f" [Score: {result.match_score:.1f}]" if result.match_score else ""
            url = result.url or ""
            source = result.source or "unknown"

            digest_parts.append(f"{i}. [{title}]({url}){score}")
            if result.summary:
                digest_parts.append(f"   _{result.summary[:150]}_")
            digest_parts.append(f"   `source: {source}`")

        digest_parts.append("")
        digest_parts.append(f"---\n{run.discovered_count} discovered · {run.sent_count} in digest")

        return "\n".join(digest_parts)

    def assemble_markdown(
        self,
        agent: Agent,
        run: AgentRun,
        results: list[AgentResult],
    ) -> str:
        raw = self.assemble(agent, run, results)
        try:
            llm_version = self._llm.generate_digest(
                summary=run.summary,
                results_text=self._results_text(results[:15]),
                agent_name=agent.name,
            )
            if llm_version:
                return llm_version
        except Exception as exc:
            logger.warning("LLM digest generation failed, using template: %s", exc)

        return raw

    def _results_text(self, results: list[AgentResult]) -> str:
        lines = []
        for r in results:
            score = f"{r.match_score:.1f}" if r.match_score else "N/A"
            lines.append(f"- {r.title} | Score: {score} | {r.url}")
        return "\n".join(lines)

    def _empty_digest(self, agent: Agent) -> str:
        return (
            f"*{agent.name}* — {agent.get_type_display()}\n"
            f"`{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}`\n\n"
            "_No new results in this digest period._"
        )
