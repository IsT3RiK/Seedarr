"""
Structured Logging Service

Provides JSON-formatted structured logging with correlation context
for request tracing and file processing tracking.

Features:
- JSON log formatter for machine-parseable output
- Request correlation via X-Request-ID
- File entry correlation for pipeline tracking
- Context propagation via contextvars
"""

import json
import logging
import uuid
from contextvars import ContextVar
from datetime import datetime
from typing import Optional, Dict, Any


# Context variables for correlation
request_id_var: ContextVar[Optional[str]] = ContextVar('request_id', default=None)
file_entry_id_var: ContextVar[Optional[int]] = ContextVar('file_entry_id', default=None)
extra_context_var: ContextVar[Dict[str, Any]] = ContextVar('extra_context', default={})


def get_request_id() -> Optional[str]:
    """Get the current request ID from context."""
    return request_id_var.get()


def set_request_id(request_id: Optional[str]) -> None:
    """Set the request ID in context."""
    request_id_var.set(request_id)


def get_file_entry_id() -> Optional[int]:
    """Get the current file entry ID from context."""
    return file_entry_id_var.get()


def set_file_entry_id(file_entry_id: Optional[int]) -> None:
    """Set the file entry ID in context."""
    file_entry_id_var.set(file_entry_id)


def get_extra_context() -> Dict[str, Any]:
    """Get extra context data."""
    return extra_context_var.get()


def set_extra_context(context: Dict[str, Any]) -> None:
    """Set extra context data."""
    extra_context_var.set(context)


def add_extra_context(**kwargs) -> None:
    """Add key-value pairs to extra context."""
    current = extra_context_var.get().copy()
    current.update(kwargs)
    extra_context_var.set(current)


def clear_context() -> None:
    """Clear all context variables."""
    request_id_var.set(None)
    file_entry_id_var.set(None)
    extra_context_var.set({})


def generate_request_id() -> str:
    """Generate a new unique request ID."""
    return str(uuid.uuid4())[:8]


class JSONLogFormatter(logging.Formatter):
    """
    JSON formatter for structured logging output.

    Produces machine-parseable JSON logs with correlation IDs
    and extra context data.
    """

    def __init__(self, include_extra: bool = True):
        super().__init__()
        self.include_extra = include_extra

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record as JSON."""
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add correlation IDs from context
        request_id = get_request_id()
        if request_id:
            log_data["request_id"] = request_id

        file_entry_id = get_file_entry_id()
        if file_entry_id:
            log_data["file_entry_id"] = file_entry_id

        # Add extra context
        if self.include_extra:
            extra_context = get_extra_context()
            if extra_context:
                log_data["context"] = extra_context

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
            }

        # Add any extra attributes from the record
        if hasattr(record, 'extra_data') and record.extra_data:
            log_data["extra"] = record.extra_data

        return json.dumps(log_data, default=str)


class StructuredLogAdapter(logging.LoggerAdapter):
    """
    Logger adapter that automatically includes correlation context.

    Usage:
        logger = get_structured_logger(__name__)
        logger.info("Processing file", extra_data={"size": 1024})
    """

    def process(self, msg, kwargs):
        """Add correlation IDs to log record."""
        extra = kwargs.get('extra', {})

        # Add correlation IDs
        request_id = get_request_id()
        if request_id:
            extra['request_id'] = request_id

        file_entry_id = get_file_entry_id()
        if file_entry_id:
            extra['file_entry_id'] = file_entry_id

        # Add extra context
        extra_context = get_extra_context()
        if extra_context:
            extra['context'] = extra_context

        # Handle extra_data parameter
        if 'extra_data' in kwargs:
            extra['extra_data'] = kwargs.pop('extra_data')

        kwargs['extra'] = extra
        return msg, kwargs


def get_structured_logger(name: str) -> StructuredLogAdapter:
    """
    Get a structured logger with correlation support.

    Args:
        name: Logger name (typically __name__)

    Returns:
        StructuredLogAdapter instance
    """
    logger = logging.getLogger(name)
    return StructuredLogAdapter(logger, {})


class CorrelationContext:
    """
    Context manager for setting correlation IDs.

    Usage:
        with CorrelationContext(request_id="abc123", file_entry_id=42):
            logger.info("This log will include correlation IDs")
    """

    def __init__(
        self,
        request_id: Optional[str] = None,
        file_entry_id: Optional[int] = None,
        **extra_context
    ):
        self.request_id = request_id
        self.file_entry_id = file_entry_id
        self.extra_context = extra_context
        self._old_request_id = None
        self._old_file_entry_id = None
        self._old_extra_context = None

    def __enter__(self):
        # Save old values
        self._old_request_id = get_request_id()
        self._old_file_entry_id = get_file_entry_id()
        self._old_extra_context = get_extra_context()

        # Set new values
        if self.request_id:
            set_request_id(self.request_id)
        if self.file_entry_id:
            set_file_entry_id(self.file_entry_id)
        if self.extra_context:
            set_extra_context(self.extra_context)

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Restore old values
        set_request_id(self._old_request_id)
        set_file_entry_id(self._old_file_entry_id)
        set_extra_context(self._old_extra_context or {})
        return False


def setup_json_logging(
    logger_name: Optional[str] = None,
    level: int = logging.INFO,
    json_output: bool = True
) -> logging.Handler:
    """
    Set up JSON logging for a logger.

    Args:
        logger_name: Logger name (None for root logger)
        level: Minimum log level
        json_output: Whether to output JSON (True) or plain text (False)

    Returns:
        The configured handler
    """
    logger = logging.getLogger(logger_name) if logger_name else logging.getLogger()

    handler = logging.StreamHandler()
    handler.setLevel(level)

    if json_output:
        handler.setFormatter(JSONLogFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            '%(asctime)s [%(levelname)s] %(name)s:%(lineno)d - %(message)s'
        ))

    logger.addHandler(handler)
    return handler
