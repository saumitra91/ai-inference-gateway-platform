from __future__ import annotations

import logging

from apps.observability.context import get_request_id


class RequestIdFilter(logging.Filter):
    """Inject `request_id` into log records for JSON formatters."""

    def filter(self, record: logging.LogRecord) -> bool:
        rid = get_request_id()
        record.request_id = rid or "-"
        return True
