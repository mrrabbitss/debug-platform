GW/AP Debug Platform - Windows 11 Portable Edition
===================================================

Recommended startup
-------------------
1. Extract the entire ZIP to a writable local directory.
2. Double-click start.bat.
3. The browser opens http://127.0.0.1:8080/ after the backend is ready.
4. Keep the console window open. Press Ctrl+C to stop the platform.

Built-in offline demo
---------------------
Open Cases and click "Import AP offline demo". The action idempotently creates
a persistent synthetic GW/AP case with 74 parsed events, three-tier triage,
expandable repeated hits, exact source-line jumps, fault-tree coverage and a
report preview. The displayed screening plans, diagnosis, 321,453-token usage
and trace come from a previously successful real GLM-5.2 run through
wawapii.com. Importing the snapshot sends no content to a model. Credentials,
raw prompts and private method-document bodies are not included.

This package already contains a Python runtime, backend dependencies and the
built Vue frontend. The target computer does not need Python, Node.js, pip,
npm or Docker, and therefore does not need pip/npm proxy configuration.

CodeAgent: one-click CLI startup
-------------------------------
Double-click start_codeagent.bat (or its Start menu shortcut). Keep your own
installed and logged-in CodeAgent. The package starts its bundled backend and
installed GGUF retrieval components, then adds gw-ap-debug to that CLI session.
Other MCP servers are not disabled; .cac, model login and proxy settings are
not overwritten. No separate Skill import, Python, Node or pip setup is needed.
CLI-created files go to data\workspace, outside the immutable application tree.
If the client cannot be found, enter its full executable/script path once.

start_codeagent.bat --cli-command "D:\Tools\CodeAgentCLI\codeagent.exe"
start_codeagent.bat --check
start_codeagent.bat --no-local-retrieval

Local unkeyed Web/CLI share a CurrentUser DPAPI-protected MCP token under
data\.launcher. Existing API-key/RBAC settings still require valid credentials.
Normal CLI exit stops only the backend/models started by this session. A reused
Web server stays running. Use matching --port/--data-root/--env-file arguments
when connecting to a customized Web launch. The CLI model owns reasoning;
GGUF Embedding/Reranker are retrieval models, not a Chat model.

Claude Code / Codex Skill + MCP (optional manual integration)
-------------------------------
The canonical gw-ap-debug Skill and its installer are included under
agent-skills\gw-ap-debug and scripts\. The launcher publishes MCP on the same
loopback port as the Web application, so start.bat --port 18080 uses
http://127.0.0.1:18080/mcp.

Set MCP_BEARER_TOKEN in %LOCALAPPDATA%\GWAPDebugPlatform\.env, restart the
platform, then set DEBUGPLATFORM_MCP_TOKEN to the same value in PowerShell and
run:

scripts\install_agent_skill_mcp.ps1 -Client All -McpUrl http://127.0.0.1:8080/mcp

Use -Client Claude or -Client Codex to install only one client. If the platform
uses --port, put that same port in -McpUrl. The CLI model performs reasoning;
the local backend supplies scoped evidence and stores the validated result.

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
all in-process native model packages, avoiding DLL failures such as WinError
1114. A Core-only build starts with the built-in Hashing Embedding and a
disabled Reranker.

The Offline GGUF edition additionally contains BGE Embedding and Qwen3
Reranker model components. It launches each model through an isolated
llama.cpp sidecar on a dynamic 127.0.0.1 port, waits for /health, and then
starts FastAPI. A new random sidecar API key is generated for each run. If a
sidecar fails, the platform remains available and falls back for that task.

Useful GGUF diagnostics:
start.bat --check --check-models
    Verify package integrity and require both GGUF sidecars to load.

start.bat --no-local-retrieval
    Temporarily use the Core retrieval fallbacks without starting sidecars.

Configure model APIs in System Settings. ENGINEER users manage their own private
Chat profiles. ADMIN/EXPERT profiles default to shared, with a private option;
even ADMIN cannot view or use another user's private profile. Personal model
selection takes priority over the shared default. API Key values are not returned.

Valid HTTP(S) Base URLs and Chat proxies work with intranet, localhost and public
hosts in both production and development. No endpoint allowlist, private-address
opt-in or enable-private-endpoints script is required. Legacy
MODEL_ENDPOINT_ALLOWLIST / MODEL_ALLOW_PRIVATE_ENDPOINTS values no longer gate
API requests. URL syntax, TLS checks and user egress consent still apply.

Only ADMIN may configure global Embedding/Reranker or GGUF models. EXPERT may use
them and rebuild knowledge indexes. GGUF sidecar identity and runtime credentials
are still validated; sidecars remain managed by the launcher and are never
imported into the platform Python process.

System Settings also provides a managed weight downloader for BGE Base and
Qwen3 Reranker. It supports an optional explicit proxy and stores completed
files under the external data directory. Downloading weights does not install
Torch or make the in-process local provider available.
