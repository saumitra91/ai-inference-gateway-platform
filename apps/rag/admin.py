from django.contrib import admin

from .models import Document


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ["original_filename", "status", "page_count", "chunk_count", "file_size_bytes", "created_at"]
    list_filter = ["status", "created_at"]
    search_fields = ["original_filename"]
    readonly_fields = ["id", "created_at", "updated_at"]
