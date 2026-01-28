"""
In-Memory Log Store Service

Provides a centralized logging system that captures application logs
and makes them available for the web UI.

Features:
- Custom logging handler that stores logs in memory
- Configurable maximum log entries (prevents memory overflow)
- Log filtering by level
- Log clearing and export functionality
- Thread-safe operations
- Structured logging with correlation IDs (request_id, file_entry_id)
- JSON export for machine parsing
"""

import json
import logging
from datetime import datetime
from collections import deque
from threading import Lock
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, asdict, field


@dataclass
class LogEntry:
    """Represents a single log entry with structured fields."""
    timestamp: str
    level: str
    message: str
    logger_name: str = ""
    request_id: Optional[str] = None
    file_entry_id: Optional[int] = None
    extra_data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for template rendering."""
        result = asdict(self)
        # Remove None values for cleaner output
        return {k: v for k, v in result.items() if v is not None and v != {}}

    def to_json(self) -> str:
        """Convert to JSON string for machine parsing."""
        return json.dumps(self.to_dict(), default=str)


class LogStore:
    """
    Singleton log store that maintains logs in memory.

    Thread-safe implementation using a deque with max length
    to prevent unbounded memory growth.
    """

    _instance: Optional['LogStore'] = None
    _lock = Lock()

    def __new__(cls, max_entries: int = 1000):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, max_entries: int = 5000):  # Increased from 1000 to 5000
        if self._initialized:
            return

        self._entries: deque = deque(maxlen=max_entries)
        self._entry_lock = Lock()
        self._initialized = True

    def add_entry(
        self,
        level: str,
        message: str,
        logger_name: str = "",
        request_id: Optional[str] = None,
        file_entry_id: Optional[int] = None,
        extra_data: Optional[Dict[str, Any]] = None
    ) -> None:
        """Add a log entry to the store with optional structured fields."""
        entry = LogEntry(
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            level=level.upper(),
            message=message,
            logger_name=logger_name,
            request_id=request_id,
            file_entry_id=file_entry_id,
            extra_data=extra_data or {}
        )
        with self._entry_lock:
            self._entries.append(entry)

    def get_entries(self, limit: int = 1000) -> List[Dict[str, Any]]:  # Increased from 500 to 1000
        """Get recent log entries as dictionaries."""
        with self._entry_lock:
            entries = list(self._entries)
        # Return most recent first, limited
        return [e.to_dict() for e in reversed(entries)][:limit]

    def get_filtered_entries(self, level: str, limit: int = 1000) -> List[Dict[str, Any]]:  # Increased from 500 to 1000
        """Get log entries filtered by level."""
        if level.lower() == 'all':
            return self.get_entries(limit)

        with self._entry_lock:
            entries = list(self._entries)

        filtered = [e for e in entries if e.level.lower() == level.lower()]
        return [e.to_dict() for e in reversed(filtered)][:limit]

    def clear(self) -> int:
        """Clear all log entries. Returns count of cleared entries."""
        with self._entry_lock:
            count = len(self._entries)
            self._entries.clear()
        return count

    def get_stats(self) -> Dict[str, Any]:
        """Get log statistics."""
        with self._entry_lock:
            entries = list(self._entries)

        total = len(entries)
        errors = sum(1 for e in entries if e.level == 'ERROR')
        warnings = sum(1 for e in entries if e.level == 'WARNING')
        info = sum(1 for e in entries if e.level == 'INFO')
        success = sum(1 for e in entries if e.level == 'SUCCESS')
        debug = sum(1 for e in entries if e.level == 'DEBUG')

        # Calculate success rate (non-error percentage)
        success_rate = ((total - errors) / total * 100) if total > 0 else 100

        return {
            "total": total,
            "errors": errors,
            "warnings": warnings,
            "info": info,
            "success": success,
            "debug": debug,
            "success_rate": round(success_rate, 1)
        }

    def export_as_text(self) -> str:
        """Export all logs as formatted text with correlation IDs."""
        entries = self.get_entries(limit=10000)  # Get all

        lines = [
            "# Application Logs Export",
            f"# Generated: {datetime.now().isoformat()}",
            f"# Total Entries: {len(entries)}",
            "",
            "=" * 80,
            ""
        ]

        for entry in entries:
            # Build correlation prefix if available
            correlation_parts = []
            if entry.get('request_id'):
                correlation_parts.append(f"req:{entry['request_id']}")
            if entry.get('file_entry_id'):
                correlation_parts.append(f"file:{entry['file_entry_id']}")
            correlation = f" [{' '.join(correlation_parts)}]" if correlation_parts else ""

            lines.append(
                f"[{entry['timestamp']}] [{entry['level']}]{correlation} {entry['message']}"
            )

        if not entries:
            lines.append("[No logs available]")

        return "\n".join(lines)

    def export_as_json(self) -> str:
        """Export all logs as JSON for machine parsing."""
        entries = self.get_entries(limit=10000)

        export_data = {
            "generated": datetime.now().isoformat(),
            "total_entries": len(entries),
            "stats": self.get_stats(),
            "entries": entries
        }

        return json.dumps(export_data, indent=2, default=str)

    def get_entries_by_request_id(self, request_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Get log entries filtered by request ID."""
        with self._entry_lock:
            entries = list(self._entries)

        filtered = [e for e in entries if e.request_id == request_id]
        return [e.to_dict() for e in reversed(filtered)][:limit]

    def get_entries_by_file_entry_id(self, file_entry_id: int, limit: int = 100) -> List[Dict[str, Any]]:
        """Get log entries filtered by file entry ID."""
        with self._entry_lock:
            entries = list(self._entries)

        filtered = [e for e in entries if e.file_entry_id == file_entry_id]
        return [e.to_dict() for e in reversed(filtered)][:limit]


class LogStoreHandler(logging.Handler):
    """
    Custom logging handler that sends logs to the LogStore.

    Attach this handler to Python's logging system to capture
    application logs for the web UI.

    Supports structured logging with correlation IDs from context.
    """

    def __init__(self, log_store: Optional[LogStore] = None):
        super().__init__()
        self.log_store = log_store or LogStore()

        # Map Python log levels to UI levels
        self.level_map = {
            logging.DEBUG: 'DEBUG',
            logging.INFO: 'INFO',
            logging.WARNING: 'WARNING',
            logging.ERROR: 'ERROR',
            logging.CRITICAL: 'ERROR',  # Map CRITICAL to ERROR for UI
        }

    def emit(self, record: logging.LogRecord) -> None:
        """Process a log record and add it to the store."""
        try:
            # Import here to avoid circular imports
            from app.services.structured_logging import (
                get_request_id, get_file_entry_id, get_extra_context
            )

            level = self.level_map.get(record.levelno, 'INFO')

            # Include logger name in message for better context
            if record.name and record.name != 'root':
                message = f"[{record.name}] {self.format(record)}"
            else:
                message = self.format(record)

            # Check for success markers in message
            if any(marker in message.lower() for marker in ['success', 'completed', 'done', '✓']):
                if level == 'INFO':
                    level = 'SUCCESS'

            # Get correlation IDs from context
            request_id = get_request_id()
            file_entry_id = get_file_entry_id()
            extra_context = get_extra_context()

            # Also check for extra_data in record (from StructuredLogAdapter)
            extra_data = {}
            if extra_context:
                extra_data.update(extra_context)
            if hasattr(record, 'extra_data') and record.extra_data:
                extra_data.update(record.extra_data)

            self.log_store.add_entry(
                level=level,
                message=message,
                logger_name=record.name,
                request_id=request_id,
                file_entry_id=file_entry_id,
                extra_data=extra_data if extra_data else None
            )
        except Exception:
            self.handleError(record)


# Global log store instance
log_store = LogStore()

# Flag to track if handler is already set up
_handler_initialized = False


def get_log_store() -> LogStore:
    """Get the global log store instance."""
    global _handler_initialized
    if not _handler_initialized:
        setup_log_store_handler(logger_name=None, level=logging.DEBUG)
    return log_store


def setup_log_store_handler(logger_name: str = None, level: int = logging.INFO) -> LogStoreHandler:
    """
    Set up the log store handler on a logger.

    Args:
        logger_name: Name of logger to attach to. None for root logger.
        level: Minimum log level to capture.

    Returns:
        The configured LogStoreHandler instance.
    """
    global _handler_initialized

    # Early return if already initialized to prevent duplicates
    if _handler_initialized:
        # Find and return existing handler
        target_logger = logging.getLogger(logger_name) if logger_name else logging.getLogger()
        for existing in target_logger.handlers:
            if isinstance(existing, LogStoreHandler):
                return existing

    if logger_name:
        target_logger = logging.getLogger(logger_name)
    else:
        target_logger = logging.getLogger()

    # Avoid duplicate handlers - check all handlers including root
    for existing in target_logger.handlers:
        if isinstance(existing, LogStoreHandler):
            _handler_initialized = True
            return existing

    # Also check root logger if we're not already on it
    if logger_name:
        for existing in logging.getLogger().handlers:
            if isinstance(existing, LogStoreHandler):
                _handler_initialized = True
                return existing

    # Create new handler
    handler = LogStoreHandler(log_store)
    handler.setLevel(level)
    # Use a more detailed formatter that includes module information
    handler.setFormatter(logging.Formatter('%(message)s'))

    # Ensure logger level allows our handler to receive logs
    if target_logger.level > level or target_logger.level == logging.NOTSET:
        target_logger.setLevel(level)

    target_logger.addHandler(handler)
    _handler_initialized = True

    # Add initial log entries to confirm logging is working and test all levels
    log_store.add_entry("INFO", "Log store initialized - capturing application logs")
    log_store.add_entry("DEBUG", "[Test] Debug log level is working")
    log_store.add_entry("WARNING", "[Test] Warning log level is working")
    log_store.add_entry("ERROR", "[Test] Error log level is working")
    log_store.add_entry("SUCCESS", "[Test] Success log level is working")

    return handler


def ensure_log_store_initialized():
    """Ensure the log store handler is initialized."""
    global _handler_initialized
    if not _handler_initialized:
        setup_log_store_handler(logger_name=None, level=logging.DEBUG)
