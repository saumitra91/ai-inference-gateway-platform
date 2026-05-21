from __future__ import annotations

import json
import logging
import time
from typing import Any, AsyncGenerator

from django.conf import settings

from django.conf import settings

from apps.inference.exceptions import UpstreamHTTPError
from apps.inference.schemas import ChatCompletionRequest
from apps.inference.services.llama_cpp import LlamaCppBackend
from apps.inference.services.vllm import VLLMBackend
from apps.rag.metrics import (
    rag_completions_total,
    rag_hallucination_fallbacks_total,
    rag_retrieval_latency,
    rag_retrieved_chunks,
)

from .vector_store import search_chunks

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_TEMPLATE = """You are a precise, honest assistant. Answer the user's question using ONLY the information provided in the context below.

CONTEXT:
{context}

INSTRUCTIONS:
1. Answer based ONLY on the provided context. Do NOT use your training data.
2. If the context does not contain enough information to answer the question, say EXACTLY: "I could not find this information in the uploaded documents."
3. Do NOT make up, infer, or fabricate any information not present in the context.
4. Keep your answer concise. Include source citations when referencing specific information."""


def build_augmented_prompt(
    messages: list[dict[str, str]],
    chunks: list[dict[str, Any]],
) -> list[dict[str, str]]:
    context_parts: list[str] = []
    doc_sources: set[str] = set()

    for chunk in chunks:
        text = chunk["text"]
        meta = chunk.get("metadata", {})
        doc_name = meta.get("document_id", "unknown")
        page = meta.get("page_number", "?")
        context_parts.append(f"[Source: {doc_name}, page {page}]\n{text}")
        doc_sources.add(doc_name)

    context_text = "\n\n".join(context_parts)

    if len(context_text) > settings.RAG_MAX_CONTEXT_CHARS:
        context_text = context_text[: settings.RAG_MAX_CONTEXT_CHARS]
        logger.warning("Context truncated to %d chars", settings.RAG_MAX_CONTEXT_CHARS)

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(context=context_text)

    augmented = [{"role": "system", "content": system_prompt}]
    for msg in messages:
        if msg["role"] == "system":
            continue
        augmented.append(msg)

    return augmented


def format_citations(chunks: list[dict[str, Any]]) -> list[dict[str, object]]:
    seen: set[str] = set()
    citations: list[dict[str, object]] = []
    for c in chunks:
        meta = c.get("metadata", {})
        doc_id = meta.get("document_id", "unknown")
        if doc_id not in seen:
            seen.add(doc_id)
            citations.append({
                "document_id": doc_id,
                "page": meta.get("page_number", "?"),
                "relevance_score": c.get("score", 0.0),
            })
    return citations


async def rag_completion_stream(
    messages: list[dict[str, str]],
    model: str = "default",
    max_tokens: int = 1024,
    temperature: float = 0.7,
    top_p: float = 0.9,
    document_ids: list[str] | None = None,
) -> AsyncGenerator[str, None]:
    start_time = time.monotonic()

    query_text = _extract_last_user_message(messages)

    retrieval_start = time.monotonic()
    chunks = search_chunks(
        query_text=query_text,
        top_k=settings.RAG_TOP_K,
        document_ids=document_ids,
    )
    retrieval_duration = time.monotonic() - retrieval_start
    rag_retrieval_latency.observe(retrieval_duration)
    rag_retrieved_chunks.observe(len(chunks))

    logger.info(
        "RAG retrieval: %d chunks in %.3fs (query: %.50s...)",
        len(chunks), retrieval_duration, query_text,
    )

    min_score = settings.RAG_MIN_SCORE
    chunks = [c for c in chunks if c.get("score", 0) >= min_score]

    if not chunks:
        rag_hallucination_fallbacks_total.inc()
        logger.warning("No chunks above threshold (%.2f) for query", min_score)
        yield f"data: {json.dumps({'type': 'rag_metadata', 'found': False, 'citations': []})}\n\n"
        yield f"data: {json.dumps({'id': '', 'object': 'chat.completion.chunk', 'choices': [{'delta': {'content': 'I could not find this information in the uploaded documents.'}, 'index': 0}]})}\n\n"
        yield "data: [DONE]\n\n"
        return

    rag_completions_total.inc()

    citations = format_citations(chunks)
    yield f"data: {json.dumps({'type': 'rag_metadata', 'found': True, 'chunks_retrieved': len(chunks), 'citations': citations})}\n\n"

    augmented_messages = build_augmented_prompt(messages, chunks)

    request = ChatCompletionRequest(
        model=model,
        messages=[{"role": m["role"], "content": m["content"]} for m in augmented_messages],
        stream=True,
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
    )
    request.apply_defaults_and_clamp()

    est_prompt_tokens = sum(len(m.get("content", "")) for m in augmented_messages) // 4
    logger.info(
        "RAG prompt: %d messages, ~%d est tokens, max_tokens=%d",
        len(augmented_messages), est_prompt_tokens, request.max_tokens,
    )

    backend: LlamaCppBackend | VLLMBackend
    rag_backend = getattr(settings, "DEFAULT_INFERENCE_BACKEND", "llamacpp")
    if rag_backend == "vllm":
        backend = VLLMBackend()
    else:
        backend = LlamaCppBackend()
    try:
        async for chunk in backend.stream_chat_completion(request):
            yield chunk.decode("utf-8")
    except UpstreamHTTPError as exc:
        body_text = exc.body.decode("utf-8", errors="replace")
        logger.error(
            "upstream rejected RAG prompt: status=%d body=%s",
            exc.status_code, body_text,
        )
        # Retry with reduced context on 400 (likely context overflow)
        if exc.status_code == 400:
            logger.warning("Retrying RAG completion with reduced context")
            truncated = _truncate_context(augmented_messages, max_chars=2000)
            retry_request = ChatCompletionRequest(
                model=model,
                messages=[{"role": m["role"], "content": m["content"]} for m in truncated],
                stream=True,
                temperature=temperature,
                top_p=top_p,
                max_tokens=min(max_tokens, 128),
            )
            retry_request.apply_defaults_and_clamp()
            async for chunk in backend.stream_chat_completion(retry_request):
                yield chunk.decode("utf-8")
            return
        raise

    duration = time.monotonic() - start_time
    logger.info("RAG completion finished in %.2fs", duration)


def _truncate_context(messages: list[dict[str, str]], max_chars: int = 2000) -> list[dict[str, str]]:
    truncated: list[dict[str, str]] = []
    for msg in messages:
        if msg["role"] == "system":
            content = msg["content"]
            if len(content) > max_chars:
                content = content[:max_chars] + "\n\n[Context truncated due to length]"
            truncated.append({"role": "system", "content": content})
        else:
            truncated.append(msg)
    return truncated


def _extract_last_user_message(messages: list[dict[str, str]]) -> str:
    for msg in reversed(messages):
        if msg["role"] == "user":
            return msg["content"]
    return ""
