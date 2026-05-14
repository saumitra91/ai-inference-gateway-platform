from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid

from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, HttpRequest, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.http import require_GET, require_POST

from apps.rag.metrics import rag_documents_ready
from apps.rag.models import Document

from .services.document_processor import process_document
from .services.rag_completion import rag_completion_stream
from .services.vector_store import health as chroma_health

logger = logging.getLogger(__name__)


@method_decorator(login_required, name="dispatch")
class RagChatView(View):
    def get(self, request: HttpRequest) -> HttpResponse:
        docs = Document.objects.filter(status=Document.Status.READY).order_by("-created_at")
        return render(request, "rag/chat.html", {"documents": docs})


@method_decorator(login_required, name="dispatch")
class RagDocumentsView(View):
    def get(self, request: HttpRequest) -> HttpResponse:
        docs = Document.objects.all().order_by("-created_at")
        return render(request, "rag/documents.html", {"documents": docs})


@require_GET
@login_required
def rag_document_list_api(request: HttpRequest) -> JsonResponse:
    docs = Document.objects.all().order_by("-created_at")
    data = [
        {
            "id": str(d.id),
            "original_filename": d.original_filename,
            "file_size_bytes": d.file_size_bytes,
            "page_count": d.page_count,
            "chunk_count": d.chunk_count,
            "status": d.status,
            "error_message": d.error_message,
            "created_at": d.created_at.isoformat(),
        }
        for d in docs
    ]
    return JsonResponse({"documents": data})


@require_GET
@login_required
def rag_document_status_api(request: HttpRequest, doc_id: str) -> JsonResponse:
    try:
        doc = Document.objects.get(id=doc_id)
    except Document.DoesNotExist:
        return JsonResponse({"error": "not found"}, status=404)

    return JsonResponse({
        "id": str(doc.id),
        "status": doc.status,
        "page_count": doc.page_count,
        "chunk_count": doc.chunk_count,
        "error_message": doc.error_message,
    })


@require_GET
@login_required
def rag_document_file(request: HttpRequest, doc_id: str) -> HttpResponse:
    try:
        doc = Document.objects.get(id=doc_id)
    except Document.DoesNotExist:
        return JsonResponse({"error": "not found"}, status=404)

    if not os.path.exists(doc.file_path):
        return JsonResponse({"error": "file not found on disk"}, status=404)

    return FileResponse(
        open(doc.file_path, "rb"),
        filename=doc.original_filename,
        content_type="application/pdf",
    )


@sync_to_async
def _save_upload_sync(uploaded_file, request) -> Document:
    file_id = str(uuid.uuid4())
    safe_filename = f"{file_id}.pdf"
    file_path = os.path.join(settings.MEDIA_ROOT, safe_filename)

    os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
    with open(file_path, "wb") as f:
        for chunk in uploaded_file.chunks():
            f.write(chunk)

    return Document.objects.create(
        original_filename=uploaded_file.name,
        file_path=file_path,
        file_size_bytes=uploaded_file.size,
        uploaded_by=request.user,
        status=Document.Status.UPLOADED,
    )


@require_POST
@login_required
async def rag_document_upload(request: HttpRequest) -> JsonResponse:
    if "file" not in request.FILES:
        return JsonResponse({"error": "No file provided"}, status=400)

    uploaded_file = request.FILES["file"]
    if not uploaded_file.name.lower().endswith(".pdf"):
        return JsonResponse({"error": "Only PDF files are supported"}, status=400)

    if uploaded_file.size > 50 * 1024 * 1024:
        return JsonResponse({"error": "File exceeds 50 MB limit"}, status=400)

    doc = await _save_upload_sync(uploaded_file, request)

    asyncio.ensure_future(process_document(str(doc.id)))

    return JsonResponse({
        "id": str(doc.id),
        "status": doc.status,
        "message": "Document uploaded and processing started",
    })


@require_GET
@login_required
def rag_health(request: HttpRequest) -> JsonResponse:
    chroma_ok = chroma_health()
    doc_count = Document.objects.filter(status=Document.Status.READY).count()
    rag_documents_ready.set(doc_count)
    return JsonResponse({
        "chromadb": chroma_ok,
        "documents_ready": doc_count,
        "rag_enabled": settings.RAG_ENABLED,
    })


@require_POST
@login_required
async def rag_chat_completions(request: HttpRequest) -> HttpResponse:
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    messages = body.get("messages", [])
    if not messages:
        return JsonResponse({"error": "messages is required"}, status=400)

    model = body.get("model", "default")
    max_tokens = int(body.get("max_tokens", 512))
    temperature = float(body.get("temperature", 0.7))
    top_p = float(body.get("top_p", 0.9))
    document_ids = body.get("document_ids")

    async def event_stream():
        try:
            async for event in rag_completion_stream(
                messages=messages,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                document_ids=document_ids,
            ):
                yield event
        except Exception as exc:
            logger.exception("RAG stream error")
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"

    return StreamingHttpResponse(
        event_stream(),
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@require_GET
@login_required
def rag_document_delete(request: HttpRequest, doc_id: str) -> JsonResponse:
    try:
        doc = Document.objects.get(id=doc_id)
    except Document.DoesNotExist:
        return JsonResponse({"error": "not found"}, status=404)

    from .services.vector_store import delete_document_chunks

    delete_document_chunks(str(doc.id))

    if os.path.exists(doc.file_path):
        os.remove(doc.file_path)

    was_ready = doc.status == Document.Status.READY
    doc.delete()

    if was_ready:
        rag_documents_ready.dec()

    logger.info("Deleted document %s (%s)", doc_id, doc.original_filename)
    return JsonResponse({"message": "deleted"})
