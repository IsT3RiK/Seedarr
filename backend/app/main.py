"""
FastAPI Main Application for Seedarr v2.0

This module defines the main FastAPI application entry point with:
- API route registration
- Template configuration (Jinja2)
- Database session management
- CORS middleware
- Static file serving
- Lifespan context manager for startup/shutdown tasks
- Hot reload support for development mode

Entry Point:
    Run with: uvicorn backend.app.main:app --reload
    Dev Mode: python backend/dev.py
"""

import asyncio
import os
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
import time

from app.models.base import Base
from app.database import engine, get_db
from app.models.settings import Settings
from app.services.log_store import setup_log_store_handler
from app.services.structured_logging import (
    set_request_id, clear_context, generate_request_id
)

# Configure logging - capture ALL logs including uvicorn
# Set up logging to capture all application and server logs for the web UI

# Only set up basicConfig if no handlers exist yet
root_logger = logging.getLogger()
if not root_logger.handlers:
    logging.basicConfig(
        level=logging.DEBUG,  # Capture all log levels
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
else:
    root_logger.setLevel(logging.DEBUG)  # Set to DEBUG to capture everything

# Add log store handler to capture logs for web UI
# This will capture logs from ALL modules including uvicorn, fastapi, etc.
setup_log_store_handler(logger_name=None, level=logging.DEBUG)

# Also explicitly attach handler to uvicorn loggers to ensure they're captured
for uvicorn_logger_name in ["uvicorn", "uvicorn.error", "uvicorn.access"]:
    uvicorn_logger = logging.getLogger(uvicorn_logger_name)
    uvicorn_logger.setLevel(logging.INFO)  # Uvicorn at INFO level
    # Ensure propagation is enabled so logs reach the root logger
    uvicorn_logger.propagate = True

# Silence noisy debug messages
logging.getLogger("watchfiles.main").setLevel(logging.WARNING)
logging.getLogger("python_multipart.multipart").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


# HTTP Request Logging Middleware with X-Request-ID correlation
class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware to log all HTTP requests and responses with correlation IDs.

    Logs:
    - Request method, path, and client IP
    - Response status code and processing time
    - Errors and exceptions

    Correlation:
    - Extracts or generates X-Request-ID for request tracing
    - Sets correlation context for structured logging
    """

    async def dispatch(self, request: Request, call_next):
        # Skip logging for static files and WebSocket connections
        if request.url.path.startswith("/static") or request.url.path == "/__hot_reload__":
            return await call_next(request)

        # Extract or generate request ID for correlation
        request_id = request.headers.get("X-Request-ID") or generate_request_id()
        set_request_id(request_id)

        # Log incoming request
        client_ip = request.client.host if request.client else "unknown"
        logger.info(f"🌐 [{request_id}] {request.method} {request.url.path} from {client_ip}")

        # Process request and measure time
        start_time = time.time()
        try:
            response = await call_next(request)
            process_time = (time.time() - start_time) * 1000  # Convert to ms

            # Add request ID to response headers
            response.headers["X-Request-ID"] = request_id

            # Log response
            status_emoji = "✓" if response.status_code < 400 else "✗"
            logger.info(
                f"   [{request_id}] {status_emoji} {response.status_code} "
                f"({process_time:.2f}ms)"
            )

            return response
        except Exception as e:
            process_time = (time.time() - start_time) * 1000
            logger.error(
                f"   [{request_id}] ✗ Request failed after {process_time:.2f}ms: "
                f"{type(e).__name__}: {e}"
            )
            raise
        finally:
            # Clear context after request is complete
            clear_context()


# Initialize hot reload for development mode
hot_reload = None
if os.getenv("DEV_MODE") == "true":
    try:
        from arel import HotReload, Path as ArelPath
        # Use absolute paths to avoid path resolution issues
        base_dir = Path(__file__).parent.parent
        watch_paths = []
        for path_name in ["app", "templates", "static"]:
            full_path = base_dir / path_name
            if full_path.exists():
                watch_paths.append(ArelPath(str(full_path)))
            else:
                logger.warning(f"⚠ Hot reload: Path {full_path} does not exist, skipping")

        if watch_paths:
            hot_reload = HotReload(paths=watch_paths)
            logger.info(f"✓ Hot reload initialized for {len(watch_paths)} paths")
        else:
            logger.warning("⚠ Hot reload: No valid paths found, hot reload disabled")
    except ImportError:
        logger.warning(
            "⚠ arel package not found. Hot reload disabled. "
            "Install with: pip install arel>=0.4.0"
        )
    except Exception as e:
        logger.warning(f"⚠ Hot reload initialization failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager for startup and shutdown tasks.

    Startup Tasks:
        1. Create all database tables
        2. Fetch and cache tracker tags dynamically
        3. Verify external service connectivity (optional)

    Shutdown Tasks:
        1. Cleanup resources (if needed)

    This replaces the deprecated @app.on_event("startup") pattern with
    the modern async context manager approach.
    """
    # ========== STARTUP ==========
    logger.info("=" * 60)
    logger.info("Starting Seedarr v2.0")
    logger.info("=" * 60)

    # Create all database tables
    logger.info("Creating database tables...")
    Base.metadata.create_all(bind=engine)
    logger.info("✓ Database tables created/verified")

    # Sync tracker metadata (categories and tags) in background (non-blocking)
    # This prevents startup freeze if FlareSolverr is unavailable
    async def background_metadata_sync():
        """Background task for tracker metadata synchronization."""
        try:
            db = next(get_db())
            settings = Settings.get_settings(db)
            if not settings or not settings.tracker_url or not settings.tracker_passkey:
                logger.warning(
                    "No tracker settings found in database. Metadata synchronization skipped. "
                    "Please configure settings via /settings page."
                )
                return

            from app.services.tracker_sync_service import sync_tracker_metadata
            sync_result = await sync_tracker_metadata(db)

            if sync_result.get('success'):
                logger.info(
                    f"✓ Tracker metadata synchronized: "
                    f"{sync_result['categories_synced']} categories, "
                    f"{sync_result['tags_synced']} tags"
                )
            else:
                logger.warning(
                    f"⚠ Metadata sync failed: {sync_result.get('message', 'Unknown error')}. "
                    f"Application will use cached values."
                )
        except Exception as e:
            logger.error(
                f"⚠ Error during metadata synchronization: {type(e).__name__}: {e}. "
                f"Application will continue with cached values."
            )

    # Launch metadata sync in background (does not block startup)
    logger.info("Scheduling tracker metadata sync (background)...")
    asyncio.create_task(background_metadata_sync())

    # Periodic connection health checks (startup + every 5 min)
    async def run_connection_health_loop():
        """Check Radarr/Sonarr/Prowlarr/FlareSolverr on startup then every 5 min."""
        CHECK_INTERVAL = 300  # seconds
        while True:
            try:
                db = next(get_db())
                try:
                    from app.services.connection_health_service import check_all_services
                    await check_all_services(db)
                finally:
                    db.close()
            except Exception as e:
                logger.warning(f"⚠ Connection health check error: {e}")
            await asyncio.sleep(CHECK_INTERVAL)

    logger.info("Scheduling connection health checks (startup + every 5 min)...")
    asyncio.create_task(run_connection_health_loop())

    # Start hot reload watcher in development mode
    if hot_reload:
        logger.info("Starting hot reload file watcher...")
        await hot_reload.startup()
        logger.info("✓ Hot reload watcher started")

    # Start queue worker
    try:
        from app.workers.queue_worker import start_queue_worker
        await start_queue_worker()
        logger.info("✓ Queue worker started")
    except Exception as e:
        logger.warning(f"⚠ Queue worker failed to start: {e}")

    logger.info("✓ Application startup complete")
    logger.info("=" * 60)

    # Test all log levels to ensure they appear in the logs UI
    logger.debug("🔍 Debug logging is enabled")
    logger.info("ℹ️ Info logging is enabled")
    logger.warning("⚠️ Warning logging is enabled")
    # Don't log error on startup as it might confuse users
    # logger.error("❌ Error logging is enabled")

    yield  # Application runs here

    # ========== SHUTDOWN ==========
    logger.info("Shutting down Seedarr v2.0")

    # Stop queue worker
    try:
        from app.workers.queue_worker import stop_queue_worker
        await stop_queue_worker()
        logger.info("✓ Queue worker stopped")
    except Exception as e:
        logger.warning(f"⚠ Queue worker shutdown error: {e}")

    # Stop hot reload watcher in development mode
    if hot_reload:
        logger.info("Stopping hot reload file watcher...")
        try:
            await hot_reload.shutdown()
            logger.info("✓ Hot reload watcher stopped")
        except Exception as e:
            logger.warning(f"⚠ Hot reload shutdown error (can be safely ignored): {e}")

    logger.info("✓ Shutdown complete")


# OpenAPI Tags Metadata
tags_metadata = [
    {
        "name": "dashboard",
        "description": "Dashboard and main UI pages for navigating the application.",
    },
    {
        "name": "filemanager",
        "description": "File management operations including scanning, browsing, and selecting media files.",
    },
    {
        "name": "settings",
        "description": "Application configuration management. Configure trackers, external services, and application behavior.",
    },
    {
        "name": "trackers",
        "description": "Multi-tracker management for cross-seeding support. Configure and manage tracker connections.",
    },
    {
        "name": "batch",
        "description": "Batch processing operations for uploading multiple files at once with concurrency control.",
    },
    {
        "name": "statistics",
        "description": "Upload statistics and metrics. Track success rates, processing times, and per-tracker performance.",
    },
    {
        "name": "health",
        "description": "Health check endpoints for monitoring application and service status. Kubernetes-compatible liveness/readiness probes.",
    },
    {
        "name": "prowlarr",
        "description": "Prowlarr integration for indexer management and duplicate checking across multiple trackers.",
    },
    {
        "name": "templates",
        "description": "BBCode template management. Create, edit, and manage customizable templates for torrent presentations.",
    },
]

# FastAPI application with lifespan context manager
app = FastAPI(
    title="Seedarr v2.0",
    description="""
## Seedarr v2.0 API

Automated multimedia content publishing to private trackers.

### Features
- **Media Analysis**: Automatic codec, resolution, and audio detection via MediaInfo
- **Metadata Fetching**: TMDB integration for movie/TV show information
- **Torrent Generation**: Create .torrent files with proper announce URLs
- **Multi-Tracker Support**: Upload to multiple trackers with cross-seeding
- **Batch Processing**: Process multiple files with concurrency control
- **Queue Management**: Persistent queue with priority support

### Authentication
Most endpoints require the tracker passkey to be configured in settings.
API endpoints use session-based authentication.

### Rate Limits
- TMDB API: 40 requests per 10 seconds
- Tracker API: 10 requests per second
""",
    version="2.0.0",
    lifespan=lifespan,
    openapi_tags=tags_metadata,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    license_info={
        "name": "MIT",
    },
    contact={
        "name": "Seedarr Support",
        "url": "https://github.com/seedarr/issues",
    },
)

# Add hot reload WebSocket route in development mode
if hot_reload:
    app.add_websocket_route("/__hot_reload__", hot_reload)
    logger.info("✓ Hot reload WebSocket route registered at /__hot_reload__")

# Add request logging middleware
app.add_middleware(RequestLoggingMiddleware)
logger.info("✓ Request logging middleware enabled")

# CORS middleware
# In production, restrict allowed origins for security
# Use CORS_ORIGINS environment variable (comma-separated) or default to restrictive list
cors_origins = os.getenv("CORS_ORIGINS", "").split(",") if os.getenv("CORS_ORIGINS") else ["http://localhost:8000"]
# Allow wildcard only in development mode
if os.getenv("DEV_MODE") == "true":
    cors_origins = ["*"]
    logger.info("⚠ CORS wildcard enabled (DEV_MODE=true). Disable in production!")

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Templates
# Use relative path when running from backend directory
templates_dir = "templates" if os.path.exists("templates") else "backend/templates"
templates = Jinja2Templates(directory=templates_dir)

# Static files
# Use relative path when running from backend directory
static_dir = "static" if os.path.exists("static") else "backend/static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Register API routes
from app.api import settings_routes, dashboard_routes, filemanager_routes, tracker_routes, prowlarr_routes, health_routes, batch_routes, statistics_routes, template_routes, presentation_routes, wizard_routes, config_schema_routes

# Register settings routes directly (routes already include /api prefix where needed)
app.include_router(settings_routes.router, tags=["settings"])

# Register dashboard routes (main UI navigation)
app.include_router(dashboard_routes.router, tags=["dashboard"])

# Register file manager routes
app.include_router(filemanager_routes.router, tags=["filemanager"])

# Register tracker routes (multi-tracker management)
app.include_router(tracker_routes.router, tags=["trackers"])

# Register Prowlarr integration routes
app.include_router(prowlarr_routes.router, tags=["prowlarr"])

# Register health check routes
app.include_router(health_routes.router)

# Register batch processing routes
app.include_router(batch_routes.router)

# Register statistics routes
app.include_router(statistics_routes.router, tags=["statistics"])

# Register BBCode template routes
app.include_router(template_routes.router, tags=["templates"])

# Register presentation generator routes
app.include_router(presentation_routes.router, tags=["presentations"])

# Register wizard routes
app.include_router(wizard_routes.router, tags=["wizard"])

# Register config schema routes (YAML editor)
app.include_router(config_schema_routes.router, tags=["config-schemas"])

# Root endpoint with wizard redirect
@app.get("/")
async def root():
    """
    Root endpoint that checks if wizard should be shown.

    If the application has not been configured yet (no trackers, no TMDB, no Prowlarr)
    and wizard hasn't been completed, redirects to the setup wizard.
    Otherwise redirects to the dashboard.
    """
    from fastapi.responses import RedirectResponse
    from app.database import get_db
    from app.models.settings import Settings
    from app.models.tracker import Tracker

    # Check if wizard is needed
    db = next(get_db())
    try:
        settings = Settings.get_settings(db)
        trackers = Tracker.get_all(db)

        # Show wizard if nothing is configured and wizard not completed
        if not settings.wizard_completed:
            needs_wizard = (
                not settings.prowlarr_url and
                not trackers and
                not settings.tmdb_api_key
            )
            if needs_wizard:
                return RedirectResponse(url="/wizard")
    except Exception as e:
        logger.warning(f"Error checking wizard status: {e}")
    finally:
        db.close()

    return RedirectResponse(url="/dashboard")

# Legacy health check endpoint - redirects to /health/detailed
@app.get("/health")
async def health_check():
    """
    Legacy health check endpoint.

    Redirects to /health/detailed for comprehensive health status.
    Use /health/live for liveness or /health/ready for readiness probes.
    """
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/health/detailed")

if __name__ == "__main__":
    import uvicorn
    # Get host and port from environment variables
    host = os.getenv("APP_HOST", "0.0.0.0")
    port = int(os.getenv("APP_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)
