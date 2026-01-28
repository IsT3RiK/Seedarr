# Development Mode - Live Preview & Hot Reload

**Quick-launch development workflow with automatic browser refresh**

[![Python](https://img.shields.io/badge/Python-3.11-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104-green.svg)](https://fastapi.tiangolo.com/)
[![arel](https://img.shields.io/badge/arel-0.4.0-orange.svg)](https://github.com/florimondmanca/arel)

---

## 📋 Table of Contents

- [Overview](#overview)
- [Quick Start](#quick-start)
- [Command Reference](#command-reference)
- [How It Works](#how-it-works)
- [File Watching Behavior](#file-watching-behavior)
- [WebSocket Connection](#websocket-connection)
- [Production Safety](#production-safety)
- [Troubleshooting](#troubleshooting)
- [Advanced Configuration](#advanced-configuration)

---

## 🎯 Overview

Development Mode provides a streamlined developer experience with automatic browser refresh when you modify code, templates, or static files. This eliminates the need to manually reload your browser during development.

**Key Features:**
- 🚀 **One-Command Startup** - Single script launches everything with optimal settings
- 🔄 **Smart File Watching** - Monitors Python, HTML, CSS, JavaScript, and configuration files
- ⚡ **Instant Feedback** - Browser auto-refreshes within 2 seconds of file changes
- 🔌 **WebSocket Hot Reload** - Persistent connection with automatic reconnection
- 🛡️ **Production Safe** - Automatically disabled when `DEV_MODE` is not set

**What Gets Reloaded:**
- **Python files (`.py`)** → Server restart + browser refresh
- **Templates (`.html`, `.jinja2`)** → Browser refresh only (no server restart)
- **Static files (`.css`, `.js`)** → Browser refresh only
- **Config files (`.json`, `.yaml`)** → Server restart + browser refresh

---

## 🚀 Quick Start

### Prerequisites

Ensure you have the required packages installed:

```bash
pip install -r backend/requirements.txt
```

This installs:
- `arel>=0.4.0` - WebSocket-based hot reload server
- `watchfiles>=0.21.0` - Efficient file change detection
- `uvicorn[standard]>=0.24.0` - ASGI server with WebSocket support

### Starting Development Mode

From the project root directory:

```bash
python backend/dev.py
```

You should see:

```
============================================================
Seedarr v2.0 - Development Mode
============================================================
Server: http://127.0.0.1:8000
Hot Reload WebSocket: ws://127.0.0.1:8000/__hot_reload__
Log Level: INFO
Watching directories: backend
Watching file types: *.py, *.html, *.jinja2, *.css, *.js, *.json, *.yaml
============================================================

Press CTRL+C to stop the server
```

### Verify Hot Reload is Working

1. Open your browser to http://localhost:8000/settings
2. Open browser console (F12)
3. Look for the message: `[Hot Reload] Connected`
4. Edit any template file in `backend/templates/`
5. Save the file
6. Browser should automatically refresh

✅ **Success!** You're now in live preview mode.

---

## 📖 Command Reference

### Basic Usage

```bash
python backend/dev.py [OPTIONS]
```

### Available Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--port` | `int` | `8000` | Server port to bind to |
| `--host` | `str` | `127.0.0.1` | Server host address |
| `--verbose` | `flag` | `false` | Enable debug logging |
| `--watch-dir` | `str` | (none) | Additional directory to watch (can be used multiple times) |

### Examples

**Start on default port 8000:**
```bash
python backend/dev.py
```

**Start on custom port:**
```bash
python backend/dev.py --port 8080
```

**Enable verbose logging:**
```bash
python backend/dev.py --verbose
```

**Watch additional directories:**
```bash
python backend/dev.py --watch-dir=../docs
```

**Combine multiple options:**
```bash
python backend/dev.py --port 8080 --verbose --watch-dir=../frontend
```

### Help Command

```bash
python backend/dev.py --help
```

---

## 🔧 How It Works

Development Mode combines three components to provide seamless hot reload:

### 1. Development Launch Script (`backend/dev.py`)

The `dev.py` script:
- Sets environment variable `DEV_MODE=true` to enable hot reload features
- Configures uvicorn with enhanced file watching
- Watches multiple file types beyond just `.py` files
- Provides clean startup messages and error handling

### 2. Hot Reload Middleware (`backend/app/middleware/hot_reload.py`)

The middleware:
- **Only activates** when `DEV_MODE=true` (safe for production)
- Injects WebSocket client script into all HTML responses
- Provides browser console logging for connection status
- Implements reconnection with exponential backoff

### 3. arel WebSocket Server

The arel library:
- Watches configured directories for file changes
- Broadcasts reload signals to all connected browsers via WebSocket
- Handles multiple concurrent browser connections
- Provides at `ws://localhost:8000/__hot_reload__` endpoint

**Flow Diagram:**

```
File Change → arel detects change → WebSocket message → Browser receives message → Page reloads
     ↓
Python file? → uvicorn restarts server → Browser reconnects → Page loads new code
     ↓
Template/CSS/JS? → No server restart → Faster reload
```

---

## 📂 File Watching Behavior

### Watched File Types

Development Mode monitors these file extensions:

| Extension | Type | Reload Behavior |
|-----------|------|-----------------|
| `.py` | Python source | Server restart + browser refresh |
| `.html` | HTML templates | Browser refresh only |
| `.jinja2` | Jinja2 templates | Browser refresh only |
| `.css` | Stylesheets | Browser refresh only |
| `.js` | JavaScript | Browser refresh only |
| `.json` | Configuration | Server restart + browser refresh |
| `.yaml` | Configuration | Server restart + browser refresh |

### Watched Directories

By default, only the `backend/` directory is watched. Use `--watch-dir` to add more:

```bash
python backend/dev.py --watch-dir=../frontend --watch-dir=../docs
```

### Ignored Files

These files and directories are **automatically excluded** from watching:

- `__pycache__/` - Python cache directories
- `*.pyc` - Compiled Python files
- `.git/` - Git repository metadata
- `node_modules/` - Node.js dependencies
- `venv/`, `.venv/` - Python virtual environments
- `.auto-claude/` - Auto-Claude working files
- `*.db`, `*.sqlite` - Database files

### Debouncing

File change events are debounced to prevent multiple rapid reloads:
- **Debounce window:** 1 second
- If multiple files change within 1 second, only one reload is triggered
- Prevents reload loops when saving auto-formats multiple files

---

## 🔌 WebSocket Connection

### Connection Lifecycle

1. **Page Load** → Browser connects to `ws://localhost:8000/__hot_reload__`
2. **Connected** → Console logs: `[Hot Reload] Connected`
3. **File Change** → Server sends reload message
4. **Reload** → Console logs: `[Hot Reload] File change detected, reloading page...`
5. **Page Refresh** → Browser reloads and reconnects

### Reconnection Strategy

If the WebSocket connection is lost (e.g., server restart), the browser automatically reconnects with **exponential backoff**:

| Attempt | Delay |
|---------|-------|
| 1st | 1 second |
| 2nd | 2 seconds |
| 3rd | 4 seconds |
| 4th+ | 8 seconds (max) |

Console message example:
```
[Hot Reload] Connection closed, attempting to reconnect...
[Hot Reload] Reconnecting in 1000ms (attempt 1)...
[Hot Reload] Connected
```

### Checking Connection Status

Open browser console (F12) and look for:

✅ **Working:**
```
[Hot Reload] Connected
```

❌ **Not Working:**
```
WebSocket connection to 'ws://localhost:8000/__hot_reload__' failed: Connection refused
```

If you see connection errors, verify:
1. Development server is running with `python backend/dev.py`
2. Environment variable `DEV_MODE=true` is set (script sets this automatically)
3. Port is not blocked by firewall

---

## 🛡️ Production Safety

### Critical: Never Enable in Production

Hot reload features are **automatically disabled** in production environments. Multiple safety mechanisms prevent accidental activation:

### Safety Mechanism 1: Environment Variable Gate

Hot reload **only activates** when `DEV_MODE=true`:

```python
# In backend/app/main.py
if os.getenv("DEV_MODE") == "true":
    hot_reload = HotReload(paths=[...])
    app.add_middleware(HotReloadMiddleware)
```

### Safety Mechanism 2: Production Command

Using the production uvicorn command **never** sets `DEV_MODE`:

```bash
# Production - Hot reload DISABLED
uvicorn backend.app.main:app

# Development - Hot reload ENABLED
python backend/dev.py
```

### Safety Mechanism 3: Middleware Guard

The middleware checks `DEV_MODE` before injecting scripts:

```python
# In hot_reload.py
if not os.getenv("DEV_MODE") == "true":
    return await call_next(request)  # Skip injection
```

### Verifying Production Safety

To confirm hot reload is disabled in production:

1. Start server without `dev.py`:
   ```bash
   uvicorn backend.app.main:app
   ```

2. Open browser to http://localhost:8000/settings

3. View page source (Ctrl+U)

4. Search for `__hot_reload__`

5. **Expected:** No WebSocket script found

6. **If found:** Check that `DEV_MODE` environment variable is not set

### Why This Matters

Enabling hot reload in production would:
- ❌ Expose WebSocket endpoint to attackers
- ❌ Waste server resources on file watching
- ❌ Inject unnecessary JavaScript into pages
- ❌ Potentially reload pages on deployment updates

**The `dev.py` script is for development only. Never use it in production deployments.**

---

## 🐛 Troubleshooting

### Problem: "Connection refused" in Browser Console

**Symptom:**
```
WebSocket connection to 'ws://localhost:8000/__hot_reload__' failed: Connection refused
```

**Possible Causes & Solutions:**

1. **Server not running**
   - Check terminal - is `uvicorn` running?
   - Restart: `python backend/dev.py`

2. **Started with wrong command**
   - Did you use `uvicorn backend.app.main:app` instead of `python backend/dev.py`?
   - Use `python backend/dev.py` to enable hot reload

3. **Port mismatch**
   - Are you accessing a different port than the server is running on?
   - Check terminal for: `Server: http://127.0.0.1:XXXX`
   - Access that exact URL in browser

### Problem: Port Already in Use

**Symptom:**
```
Error starting development server: [Errno 98] Address already in use
```

**Solutions:**

1. **Find process using port 8000:**
   ```bash
   # Linux/Mac
   lsof -i :8000

   # Windows
   netstat -ano | findstr :8000
   ```

2. **Kill the process:**
   ```bash
   # Linux/Mac
   kill -9 <PID>

   # Windows
   taskkill /PID <PID> /F
   ```

3. **Use a different port:**
   ```bash
   python backend/dev.py --port 8080
   ```

### Problem: Browser Not Auto-Refreshing

**Symptom:** File changes don't trigger browser reload

**Diagnostic Steps:**

1. **Check browser console**
   - Open DevTools (F12) → Console tab
   - Look for `[Hot Reload] Connected`
   - If missing, WebSocket connection failed (see above)

2. **Verify DEV_MODE is set**
   ```bash
   # Terminal running dev.py should show:
   DEV_MODE=true
   ```

3. **Check file is being watched**
   - Confirm file type is in watched list (`.py`, `.html`, `.jinja2`, `.css`, `.js`)
   - Check file is in watched directory (`backend/` by default)
   - Files in `.gitignore` or `__pycache__/` are not watched

4. **Verify file actually changed**
   - Save the file (Ctrl+S)
   - Check terminal for uvicorn reload message:
     ```
     WARNING:  WatchFiles detected changes in 'backend/app/main.py'. Reloading...
     ```

5. **Check for syntax errors**
   - If Python file has syntax error, server won't restart
   - Check terminal for error traceback

### Problem: Multiple Rapid Reloads

**Symptom:** Page reloads multiple times in quick succession

**Causes:**

1. **Auto-formatter triggered**
   - Your IDE may auto-format on save, changing multiple files
   - Solution: This is expected; debouncing prevents excessive reloads

2. **Watching too many directories**
   - Added `--watch-dir` for a directory with many files
   - Solution: Be selective with `--watch-dir`, exclude large directories

3. **File watcher loop**
   - Rare: server restart writes a file that triggers another restart
   - Solution: Add problematic files to `.gitignore`

### Problem: Hot Reload Script Appears in Production

**Symptom:** WebSocket script found in production HTML source

**This is a CRITICAL SECURITY ISSUE. Fix immediately:**

1. **Check environment variables**
   ```bash
   env | grep DEV_MODE
   ```
   - Should be empty in production
   - If set, remove from environment/config

2. **Verify startup command**
   - Production should use: `uvicorn backend.app.main:app`
   - NOT: `python backend/dev.py`

3. **Check middleware registration**
   - Review `backend/app/main.py`
   - Middleware should only register when `DEV_MODE=true`

### Problem: "arel" or "watchfiles" Not Found

**Symptom:**
```
ModuleNotFoundError: No module named 'arel'
```

**Solution:**

```bash
pip install -r backend/requirements.txt
```

Or install individually:
```bash
pip install arel>=0.4.0 watchfiles>=0.21.0
```

### Problem: WebSocket Disconnects Frequently

**Symptom:** Console shows repeated reconnection attempts

**Possible Causes:**

1. **Server instability**
   - Check terminal for Python errors/exceptions
   - Review application logs

2. **Network issues**
   - Using VPN or proxy that blocks WebSockets?
   - Try accessing via `127.0.0.1` instead of `localhost`

3. **Browser extension blocking WebSockets**
   - Disable ad blockers temporarily
   - Try in incognito mode

### Getting Help

If problems persist:

1. **Check server logs** - Terminal running `dev.py` shows all errors
2. **Check browser console** - F12 → Console tab for client-side errors
3. **Verify versions** - Run `pip show arel watchfiles uvicorn`
4. **Test minimal setup** - Create simple HTML page, confirm hot reload works

---

## ⚙️ Advanced Configuration

### Custom File Watching

To watch additional file types, modify `backend/dev.py`:

```python
reload_includes = [
    "*.py",
    "*.html",
    "*.jinja2",
    "*.css",
    "*.js",
    "*.json",
    "*.yaml",
    # Add your custom extensions:
    "*.md",      # Markdown files
    "*.svg",     # SVG images
    "*.toml",    # TOML config files
]
```

### Excluding Directories

To exclude specific directories from watching, modify the arel initialization in `backend/app/main.py`:

```python
from pathlib import Path

hot_reload = HotReload(
    paths=[
        Path("backend/app"),
        Path("backend/templates"),
        Path("backend/static"),
    ],
    # Exclude patterns (glob syntax)
    exclude=["**/__pycache__/**", "**/tests/**"],
)
```

### Customizing Reconnection Timing

Edit the reconnection delays in `backend/app/middleware/hot_reload.py`:

```javascript
const HOT_RELOAD_CONFIG = {
    wsUrl: '{ws_url}',
    reconnectDelays: [500, 1000, 2000, 5000],  // Custom delays in ms
    reconnectAttempt: 0,
    reconnectTimer: null
};
```

### Using with Docker

If running in Docker, ensure:

1. **Port mapping** includes WebSocket port:
   ```yaml
   # docker-compose.yml
   ports:
     - "8000:8000"
   ```

2. **Environment variable** is set:
   ```yaml
   environment:
     - DEV_MODE=true
   ```

3. **Volume mounts** for code watching:
   ```yaml
   volumes:
     - ./backend:/app/backend
   ```

4. **WebSocket URL** uses container name:
   ```javascript
   // If accessing via docker network
   const wsUrl = `ws://backend:8000/__hot_reload__`;
   ```

### Integration with IDEs

#### VS Code

Add to `.vscode/launch.json`:

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Python: Dev Server",
      "type": "python",
      "request": "launch",
      "program": "${workspaceFolder}/backend/dev.py",
      "console": "integratedTerminal",
      "justMyCode": false
    }
  ]
}
```

Now press F5 to start dev server with debugging.

#### PyCharm

1. Run → Edit Configurations
2. Add new "Python" configuration
3. Script path: `backend/dev.py`
4. Working directory: Project root
5. Click OK and run

### Performance Tuning

If hot reload is slow:

1. **Reduce watched directories**
   - Only watch directories you're actively editing
   - Remove `--watch-dir` arguments you don't need

2. **Exclude large directories**
   - Add to `.gitignore`: `node_modules/`, `venv/`, `.auto-claude/`

3. **Use SSD**
   - File watching is I/O intensive
   - SSDs provide much faster change detection

4. **Close unnecessary programs**
   - File watchers compete for system resources
   - Close other IDEs or file-watching tools

---

## 📚 Additional Resources

- **FastAPI Documentation:** https://fastapi.tiangolo.com/
- **arel GitHub:** https://github.com/florimondmanca/arel
- **uvicorn Documentation:** https://www.uvicorn.org/
- **WebSocket MDN Guide:** https://developer.mozilla.org/en-US/docs/Web/API/WebSockets_API

---

## 💡 Tips & Best Practices

### DO ✅

- **Always use `dev.py`** for development work
- **Check browser console** on first page load to confirm connection
- **Save files individually** rather than "save all" to see changes incrementally
- **Keep terminal visible** to see reload messages and catch errors early
- **Use `--verbose`** when debugging to see detailed uvicorn logs

### DON'T ❌

- **Don't use `dev.py` in production** - only for local development
- **Don't watch entire project** - be selective with `--watch-dir`
- **Don't edit files while server is restarting** - wait for reload to complete
- **Don't rely on hot reload for complex state** - some changes require full restart
- **Don't ignore WebSocket errors** - connection issues indicate deeper problems

---

**Happy developing! 🚀**

For more information, see:
- [Architecture Documentation](./ARCHITECTURE.md)
- [Migration Guide](./MIGRATION_GUIDE.md)
- [Adapter Pattern Guide](./ADAPTER_PATTERN.md)
