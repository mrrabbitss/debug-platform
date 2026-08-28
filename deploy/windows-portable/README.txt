GW/AP Debug Platform - Windows 11 Portable Edition
===================================================

Recommended startup
-------------------
1. Extract the entire ZIP to a writable local directory.
2. Double-click start.bat.
3. The browser opens http://127.0.0.1:8080/ after the backend is ready.
4. Keep the console window open. Press Ctrl+C to stop the platform.

This package already contains a Python runtime, backend dependencies and the
built Vue frontend. The target computer does not need Python, Node.js, pip,
npm or Docker, and therefore does not need pip/npm proxy configuration.

Useful commands
---------------
start.bat --check
    Verify package hashes, writable data storage and runtime isolation.

start.bat --no-browser
    Start without opening the browser automatically.

start.bat --port 18080
    Use another loopback port when 8080 is occupied.

start.bat --data-root D:\DebugPlatformData
    Override the default database and uploaded-file location.

Data and upgrades
-----------------
- Runtime data defaults to %LOCALAPPDATA%\GWAPDebugPlatform\data.
- Local settings and encrypted model credentials default to
  %LOCALAPPDATA%\GWAPDebugPlatform\.env and data\.
- Releases can be unpacked into a new directory without moving runtime data.
- Back up %LOCALAPPDATA%\GWAPDebugPlatform before a major upgrade.

Model boundary
--------------
The portable platform deliberately excludes Torch, sentence-transformers and
model weights. It starts with the built-in Hashing Embedding and a disabled
Reranker, avoiding native model DLL failures such as WinError 1114.

Configure approved Chat, Embedding or Reranker API endpoints in System
Settings. If local BGE/Qwen weights are required, run them in a separately
managed model service and connect through an API profile. Model downloads and
native inference runtimes are not installed into this platform process.

System Settings also provides a managed weight downloader for BGE Base and
Qwen3 Reranker. It supports an optional explicit proxy and stores completed
files under the external data directory. Downloading weights does not install
Torch or make the in-process local provider available.
