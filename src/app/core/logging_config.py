"""
Centralized logging configuration for EV Trip Optimizer.

Call setup_logging() once at application startup, before any other imports
that might log. Configures:
- Root logger with configurable LOG_LEVEL
- JSON formatter for production, human-readable for development
- Request/tenant correlation via contextvars
- Uvicorn log integration
"""

import logging
import sys
import uuid
from contextvars import ContextVar

from pythonjsonlogger.json import JsonFormatter
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# ── Context variables for correlation ──────────────────────────────
# request_id is set per-request by RequestIdMiddleware. Set business_id in a
# handler (business_id_var.set(...)) to tag every log line in that request with
# the tenant. Add your own vars the same way.
request_id_var: ContextVar[str] = ContextVar("request_id", default="")
business_id_var: ContextVar[str] = ContextVar("business_id", default="")


class AppJsonFormatter(JsonFormatter):
    """JSON formatter that injects correlation IDs from contextvars."""

    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)
        log_record["logger"] = record.name
        log_record["level"] = record.levelname

        rid = request_id_var.get("")
        if rid:
            log_record["request_id"] = rid
        bid = business_id_var.get("")
        if bid:
            log_record["business_id"] = bid


class AppDevFormatter(logging.Formatter):
    """Colored, human-readable formatter for local development.

    Format: TIMESTAMP LEVEL [logger] [context] message
    Only includes context fields if set.
    """

    RESET = "\033[0m"
    LEVEL_COLORS = {
        "DEBUG": "\033[36m",     # cyan
        "INFO": "\033[32m",      # green
        "WARNING": "\033[33m",   # yellow
        "ERROR": "\033[31m",     # red
        "CRITICAL": "\033[1;31m",  # bold red
    }
    DIM = "\033[2m"
    BOLD = "\033[1m"
    CYAN = "\033[36m"

    def __init__(self):
        super().__init__(
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    def format(self, record):
        # Work on a copy so multiple handlers don't interfere
        record = logging.makeLogRecord(record.__dict__)

        color = self.LEVEL_COLORS.get(record.levelname, self.RESET)
        ts = self.formatTime(record, self.datefmt)

        # Context tags
        parts = []
        rid = request_id_var.get("")
        if rid:
            parts.append(f"req={rid[:8]}")
        bid = business_id_var.get("")
        if bid:
            parts.append(f"biz={bid[:8]}")

        ctx = f" {self.CYAN}[{' '.join(parts)}]{self.RESET}" if parts else ""

        msg = record.getMessage()

        line = (
            f"{self.DIM}{ts}{self.RESET} "
            f"{color}{record.levelname:<8}{self.RESET} "
            f"{self.DIM}[{record.name}]{self.RESET}"
            f"{ctx} {msg}"
        )

        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            line += f"\n{record.exc_text}"
        if record.stack_info:
            line += f"\n{record.stack_info}"

        return line


def setup_logging(log_level: str = "INFO", json_logs: bool = True) -> None:
    """Configure logging for the entire application.

    Args:
        log_level: Root log level (DEBUG, INFO, WARNING, ERROR).
        json_logs: True for JSON output (production), False for
                   human-readable (development).
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    if json_logs:
        formatter = AppJsonFormatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    else:
        formatter = AppDevFormatter()

    # Configure root logger
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(formatter)
    root.addHandler(handler)

    # Override uvicorn's handlers to use our formatter
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.addHandler(handler)
        uv_logger.setLevel(level)
        uv_logger.propagate = False

    # Suppress noisy third-party loggers
    for name in (
        "azure",
        "azure.core.pipeline.policies.http_logging_policy",
        "azure.ai",
        "urllib3",
        "httpx",
        "httpcore",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        "Logging configured",
        extra={"log_level": log_level, "json_mode": json_logs},
    )


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Adds a unique request_id to every request.

    Sets request_id_var contextvar for structured log injection.
    Respects incoming X-Request-ID header (from load balancer).
    Adds X-Request-ID to response for client correlation.
    """

    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id", str(uuid.uuid4()))
        token = request_id_var.set(rid)
        try:
            response: Response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            request_id_var.reset(token)
