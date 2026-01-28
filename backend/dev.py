#!/usr/bin/env python
"""
Quick Launch Development Server for Seedarr v2.0

This script provides a convenient one-command startup for development mode with:
- Automatic file watching for Python, HTML, CSS, and JavaScript files
- Browser hot reload capabilities via arel WebSocket integration
- Environment variable configuration for development mode
- Enhanced logging options

Usage:
    python backend/dev.py                    # Start with defaults
    python backend/dev.py --port 8080        # Custom port
    python backend/dev.py --verbose          # Enable debug logging
    python backend/dev.py --watch-dir=../frontend  # Watch additional directories

Environment Variables Set:
    DEV_MODE=true  - Enables hot reload middleware and WebSocket endpoint
    DEBUG=true     - Enables verbose logging (when --verbose flag is used)
"""

import os
import sys
import argparse


def main():
    """Parse arguments and launch uvicorn development server."""
    parser = argparse.ArgumentParser(
        description="Start Seedarr v2.0 in development mode with hot reload",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python backend/dev.py                     Start dev server on default port 8000
  python backend/dev.py --port 8080         Start on custom port 8080
  python backend/dev.py --verbose           Enable debug logging
  python backend/dev.py --watch-dir=../docs Watch additional directory

Hot Reload Features:
  - Python files (.py): Server restart + browser refresh
  - Templates (.html, .jinja2): Browser refresh only (no server restart)
  - Static files (.css, .js): Browser refresh only
  - Configuration files (.json, .yaml): Server restart + browser refresh

WebSocket Endpoint:
  ws://localhost:8000/__hot_reload__
        """
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Server port (default: 8000)"
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Server host (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose debug logging"
    )
    parser.add_argument(
        "--watch-dir",
        action="append",
        dest="watch_dirs",
        help="Additional directories to watch for changes (can be used multiple times)"
    )

    args = parser.parse_args()

    # Add project root to Python path (needed for uvicorn reload subprocess)
    project_root = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    os.environ["PYTHONPATH"] = project_root + os.pathsep + os.environ.get("PYTHONPATH", "")

    # Set environment variables for development mode
    os.environ["DEV_MODE"] = "true"
    os.environ["DEBUG"] = "true" if args.verbose else "false"

    # Build list of file patterns to watch for reload
    reload_includes = [
        "*.py",       # Python source files
        "*.html",     # HTML templates
        "*.jinja2",   # Jinja2 templates
        "*.css",      # Stylesheets
        "*.js",       # JavaScript files
        "*.json",     # Configuration files
        "*.yaml",     # YAML configuration files
    ]

    # Build list of directories to watch
    reload_dirs = ["backend"]
    if args.watch_dirs:
        reload_dirs.extend(args.watch_dirs)

    # Print startup information
    print("=" * 60)
    print("Seedarr v2.0 - Development Mode")
    print("=" * 60)
    print(f"Server: http://{args.host}:{args.port}")
    print(f"Hot Reload WebSocket: ws://{args.host}:{args.port}/__hot_reload__")
    print(f"Log Level: {'DEBUG' if args.verbose else 'INFO'}")
    print(f"Watching directories: {', '.join(reload_dirs)}")
    print(f"Watching file types: {', '.join(reload_includes)}")
    print("=" * 60)
    print("\nPress CTRL+C to stop the server\n")

    # Launch uvicorn with development configuration
    try:
        import uvicorn
        # Change to backend directory to use app.* imports instead of backend.app.*
        os.chdir(os.path.join(project_root, "backend"))
        uvicorn.run(
            "app.main:app",
            host=args.host,
            port=args.port,
            reload=True,
            reload_includes=reload_includes,
            reload_dirs=["app", "templates", "static"],  # Use relative paths from backend/
            log_level="debug" if args.verbose else "info",
        )
    except KeyboardInterrupt:
        print("\n\nShutting down development server...")
        sys.exit(0)
    except Exception as e:
        print(f"\n\nError starting development server: {e}")
        print("\nTroubleshooting:")
        print(f"  1. Check if port {args.port} is already in use")
        print("  2. Verify uvicorn is installed: pip install uvicorn[standard]")
        print("  3. Ensure you're in the project root directory")
        sys.exit(1)


if __name__ == "__main__":
    main()
