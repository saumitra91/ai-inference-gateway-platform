from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.inference.models import InferenceRequestLog


class Command(BaseCommand):
    help = "Delete inference request logs older than LOG_RETENTION_DAYS (or --days)."

    def add_arguments(self, parser) -> None:  # type: ignore[no-untyped-def]
        parser.add_argument("--days", type=int, default=None, help="Retention window in days (defaults to settings).")
        parser.add_argument("--dry-run", action="store_true", help="Print counts only.")

    def handle(self, *args, **options) -> None:  # type: ignore[no-untyped-def]
        days = options["days"] or int(getattr(settings, "LOG_RETENTION_DAYS", 30))
        cutoff = timezone.now() - timedelta(days=days)
        qs = InferenceRequestLog.objects.filter(created_at__lt=cutoff)
        n = qs.count()
        if options["dry_run"]:
            self.stdout.write(f"Dry run: would delete {n} rows older than {cutoff.isoformat()}")
            return
        deleted, _details = qs.delete()
        self.stdout.write(f"Deleted {deleted} objects (cutoff={cutoff.isoformat()}, days={days})")
