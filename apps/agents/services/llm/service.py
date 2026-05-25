from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx
from django.conf import settings

from apps.agents.metrics import agent_llm_requests_total

logger = logging.getLogger(__name__)


class LLMService:
    _MODEL_NAMES = {
        "llamacpp": "llama-local",
        "vllm": "vllm-model",
    }

    def __init__(self, backend: str = "llamacpp"):
        self.backend = backend
        self._model_name = self._MODEL_NAMES.get(backend, "agent-llm")
        if backend == "vllm":
            base_url = getattr(settings, "VLLM_BASE_URL", "http://127.0.0.1:8005")
        else:
            base_url = getattr(settings, "LLAMA_CPP_BASE_URL", "http://127.0.0.1:8080")
        self._base_url = str(base_url).rstrip("/")
        self._client = httpx.Client(timeout=120.0)

    def _url(self) -> str:
        return f"{self._base_url}/v1/chat/completions"

    def chat(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 512,
        temperature: float = 0.3,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self._model_name,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        start = time.monotonic()
        try:
            resp = self._client.post(self._url(), json=payload, timeout=120.0)
            resp.raise_for_status()
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            agent_llm_requests_total.labels(agent_type="agent", backend=self.backend).inc()
            logger.info(
                "LLM call completed in %.2fs (%d tokens)",
                time.monotonic() - start,
                data.get("usage", {}).get("total_tokens", 0),
            )
            return content or ""
        except httpx.RequestError as exc:
            logger.error("LLM request failed: %s", exc)
            raise
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            logger.error("LLM response parse failed: %s", exc)
            raise

    def summarize(self, text: str, max_tokens: int = 256) -> str:
        messages = [
            {
                "role": "system",
                "content": "Summarize the following text concisely. Capture the key points in 2-3 sentences.",
            },
            {"role": "user", "content": text[:4000]},
        ]
        return self.chat(messages, max_tokens=max_tokens, temperature=0.3)

    def synthesize_trends(self, items_text: str, instructions: str = "") -> str:
        system = "You are a market research analyst. Synthesize the following search results into a coherent trend summary."
        if instructions:
            system += f"\n\nAdditional instructions: {instructions}"
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": f"Synthesize trends from these findings:\n\n{items_text[:6000]}",
            },
        ]
        return self.chat(messages, max_tokens=512, temperature=0.4)

    def rank_relevance(self, items_text: str, instructions: str) -> str:
        system = (
            "You are a job matching specialist. Rank the following job listings by relevance "
            "based on the given instructions. Provide a score (1-10) for each and a brief explanation."
        )
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": f"Instructions: {instructions}\n\nJobs:\n{items_text[:6000]}",
            },
        ]
        return self.chat(messages, max_tokens=512, temperature=0.2)

    def generate_digest(self, summary: str, results_text: str, agent_name: str) -> str:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a notification writer. Create a concise, informative Telegram digest "
                    "from the following agent run results. Use markdown formatting. Group similar items. "
                    "Include key findings, top matches, and direct links."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Agent: {agent_name}\n\nSummary: {summary}\n\nResults:\n{results_text[:5000]}"
                ),
            },
        ]
        return self.chat(messages, max_tokens=1024, temperature=0.5)
