from __future__ import annotations

import logging
import time

from django.core.management.base import BaseCommand

from apps.agents.scheduler import start_scheduler

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Start the APScheduler-based agent scheduler"

    def handle(self, *args, **options):
        self.stdout.write("Starting agent scheduler...")
        start_scheduler()
        self.stdout.write("Agent scheduler running. Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stdout.write("Scheduler stopped.")
