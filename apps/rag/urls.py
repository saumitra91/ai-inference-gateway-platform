from django.urls import path

from .views import RagChatView, RagDocumentsView
from . import views

urlpatterns = [
    path("chat/", RagChatView.as_view(), name="chat"),
    path("documents/", RagDocumentsView.as_view(), name="documents"),
    path("api/documents/", views.rag_document_list_api, name="api_document_list"),
    path("api/documents/upload", views.rag_document_upload, name="api_document_upload"),
    path("api/documents/<str:doc_id>/status", views.rag_document_status_api, name="api_document_status"),
    path("api/documents/<str:doc_id>/file", views.rag_document_file, name="api_document_file"),
    path("api/documents/<str:doc_id>/delete", views.rag_document_delete, name="api_document_delete"),
    path("api/completions", views.rag_chat_completions, name="api_completions"),
    path("api/health", views.rag_health, name="api_health"),
]
