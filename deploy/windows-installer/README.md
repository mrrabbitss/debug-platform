# Windows offline GGUF installer

Status: experimental build path. The current Win11 runtime semantic smoke and
an assembled ZIP self-check/fresh-data/Profile/API probe have passed. Inno Setup
6.7.1 has also produced a single-file Setup candidate with verified hash and
version metadata. The project Setup is still unsigned, and clean-machine
installation, packaged knowledge/RAG quality, upstream Golden parity and the
strict GitHub Release gate are pending. Do not call its output release-ready yet.
The tracked source of truth is
`docs/windows-offline-gguf-installer.md`.

This packaging path assembles a per-user Win11 x64 candidate containing:

- the isolated Python/FastAPI/Vue Core package;
- a CPU-only `llama-server` runtime and every DLL from its pinned release;
- the exact Microsoft VC143 x64 release CRT closure deployed app-local beside
  `llama-server`, with immutable source size/SHA, per-DLL hashes and Authenticode
  checks (licensed under Microsoft redistribution terms, not an open-source SPDX
  license);
- BAAI BGE Base Chinese v1.5 F16 GGUF Embedding, release-locked to
  `204756128` bytes and SHA-256
  `677d0074629b28c96860d258d04c5197996d3f422cba84badadd64090e085f35`;
- Qwen3 Reranker 0.6B Q8_0 GGUF from llama.cpp's official `ggml-org`
  organization, pinned to artifact revision
  `a02f48bb4f057028298c21fa033da2b30d7742d5` and SHA-256
  `22c9979ce4fbcdc5acdc310c6641c32797eff1aa980b8f7a2db8a8ea23429a48`;
- an extract-and-install ZIP and, when Inno Setup 6 is available, one Setup.exe.

Torch and sentence-transformers are never added to the application Python
runtime. Chat remains an explicitly configured remote model.

## Build-cache contract

Large binaries and model weights are build inputs, not repository files. Use
the standard-library preparation entry point with Python 3.12; it downloads
only immutable URLs from `scripts/model-runtime/model-assets.json`, verifies
every byte size/SHA-256, converts BGE with the pinned llama.cpp converter and
generates `components.lock.json` automatically:

```bat
py -3.12 scripts\model-runtime\prepare_assets.py --all
```

`components.lock.example.json` documents the resulting shape; it is not a
substitute for the generated, verified lock.

Every cache file must have an exact byte size and SHA-256. Use immutable source
revisions. The lock also records upstream URL, revision and license. All DLLs
present below `runtime.cache_directory` must appear in `files[]`; the build
refuses an incomplete or GPU-enabled runtime. Model/license paths referenced by
the component records must also appear in `files[]`.

A typical private build cache is:

```text
artifacts/build-cache/windows-x64/
├── components.lock.json
├── llama/
│   ├── llama-server.exe
│   ├── llama.dll
│   ├── ggml.dll
│   ├── ggml-base.dll
│   ├── ggml-cpu*.dll
│   ├── msvcp140*.dll
│   ├── vcruntime140*.dll
│   └── LICENSE-LLVM-OpenMP
├── models/
│   ├── bge-base-zh-v1.5-f16.gguf
│   └── reranker/
│       └── qwen3-reranker-0.6b-q8_0.gguf
└── licenses/
    ├── Apache-2.0.txt
    ├── llama.cpp-LICENSE.txt
    └── MIT.txt
```

Do not add that directory to Git. The build binds the generated lock back to
the repository's pinned asset manifest before copying anything, then runs
`llama-server --version` plus a required-flag check after assembly.

The CRT input is the immutable official Microsoft VC143 x64 base VSIX. Extraction
uses an exact release-file allowlist and rejects `debug_nonredist`; build-time
Authenticode verification must resolve every DLL to Microsoft. The runtime smoke
then enumerates the live sidecar modules and requires the three imported CRT DLLs
to load from packaged `runtime/llama`, so a developer machine's global VC runtime
cannot hide an incomplete package. Redistributors remain responsible for meeting
the [Visual Studio 2022 redistribution terms](https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution).

The Qwen base model and Apache-2.0 license remain attributable to the upstream
Qwen repository. `ggml-org` is the official llama.cpp publishing organization,
not the Qwen model author; the component lock records both the converted
artifact revision and the upstream Qwen model revision.

## Build

From a Win11 x64 checkout:

```bat
scripts\build_windows_gguf_installer.bat -ValidateCacheOnly
scripts\build_windows_gguf_installer.bat -Version 0.1.0
```

The normal build compiles the frontend, builds the isolated Core, verifies all
hashes, loads both GGUF models, starts the complete application, and produces
artifacts below `artifacts/installer`.

Useful development switches:

- `-SkipFrontendBuild`: reuse an existing `frontend/dist`.
- `-SkipModelSmoke`: test Core without loading the large models. Do not use for
  a release candidate.
- `-SkipSmokeTest`: assembly only. Do not use for a release candidate.
- `-SkipSetupExe`: produce only the staging tree and offline ZIP.
- `-RequireSetupExe`: fail unless an Inno Setup `ISCC.exe` can create the
  one-click installer.
- `-IsccPath C:\...\ISCC.exe`: select an explicit Inno Setup compiler.

Inno Setup is only a build-machine dependency. The resulting Setup.exe and ZIP
need no Python, pip, Node.js, npm, Torch, model download or administrator rights
on the target computer.

The current Win11 build used the pinned Inno Setup 6.7.1 portable compiler. Its
immutable GitHub Release downloader had a valid Authenticode signature from
`Pyrsys B.V.`. Compilation completed in 213.469 seconds and produced an unsigned
899,941,808-byte `GWAP-Debug-Platform-Setup-0.1.0-x64.exe` with SHA-256
`08499a54aabc7bf7e7e4f0e5b0256b1b79d3d60126e70bcfd7341b62c669fb1a`,
`FileVersion=0.1.0`, and `ProductName=GWAP Debug Platform`. The project Setup's
Authenticode status is `NotSigned`; the valid compiler-downloader signature does
not sign this output. Code signing and a clean Win11 install/upgrade/uninstall
matrix remain release gates.

That first full Setup candidate predates the atomic-upgrade correction below.
Its size and hash are historical compiler evidence only; rebuild the complete
installer before evaluating or signing a current candidate.

## Atomic upgrade and uninstall contract

Inno Setup must never merge package files directly into `{app}`. A merge leaves
files removed by newer versions behind, and the next `package-manifest.json`
check then correctly rejects the unexpected stale files.

The Inno source now extracts every payload file to
`{tmp}\GWAPDebugPlatformPayload`, with `package-manifest.json` as the final
`[Files]` entry. Its `AfterInstall` callback runs
`install_local.ps1 -NoLaunch -NoShortcuts`, waits for PowerShell to terminate,
and raises a fatal Setup error if the process cannot start or exits non-zero.
The shared publisher copies to a sibling staging tree, verifies the complete
manifest, keeps the prior app tree as a backup during the swap, and removes it
only after publication succeeds. Files absent from the new release therefore
cannot survive an upgrade.

ZIP and Setup publication share one named per-user mutex. A second installer
waits for at most 120 seconds and then fails with a clear timeout; Windows
releases the mutex automatically if a process crashes. After acquiring the lock,
if `{app}` is absent and exactly one `GWAPDebugPlatform.backup-<32 hex>` sibling
exists, the publisher verifies its manifest shape plus the required launcher,
model metadata and Python runtime before atomically restoring it. It refuses to
guess when multiple backup candidates exist and leaves an incomplete candidate
untouched.

Inno registers the packaged Python and llama-server paths with Restart Manager,
then owns Start Menu/Desktop shortcuts and its sibling uninstaller directory.
Uninstall is guarded to the exact per-user `{app}` path and deletes only that
immutable application tree. `%LOCALAPPDATA%\GWAPDebugPlatform` is the runtime
data root and is deliberately not an uninstall target.

## Runtime contract

The portable launcher performs the following sequence:

1. validates the immutable component paths;
2. generates a per-run random API key in a temporary state file;
3. starts Embedding and Reranker on separate dynamic `127.0.0.1` ports;
4. attaches both processes to a kill-on-close Windows Job Object;
5. waits for each `/health` endpoint;
6. exports `BUNDLED_GGUF_EMBEDDING_URL`,
   `BUNDLED_GGUF_RERANKER_URL` and `BUNDLED_GGUF_API_KEY` before FastAPI is
   imported;
7. removes the key file and terminates the complete process tree on shutdown.

One failed sidecar does not stop the Core. Its URL is omitted so the backend can
fall back to Hashing Embedding or a disabled Reranker. Logs and a content-free
status file are written under
`%LOCALAPPDATA%\GWAPDebugPlatform\data\logs\local-models`.

For a release candidate, run both:

```bat
start.bat --check
start.bat --check --check-models
```

The installer build also executes `scripts/model-runtime/smoke_runtime.py`
unless a skip switch is used. The current locked artifacts passed real native
inference: BGE returned normalized finite 768-D vectors and ranked the related
diagnostic sentence above unrelated text (`0.5609 > 0.1655`); Qwen ranked the
related sentence first (`0.9997 > 0.0001`). Pinned b10729 explicitly defines
`--reranking` as enabling the reranking endpoint, and `/v1/rerank` passed with
that argument.

This is a small semantic/API smoke, not full upstream parity or retrieval
quality proof. The asset manifest intentionally remains
`experimental_unverified`: Python conversion wheels are not yet a hash-locked
offline wheelhouse, BGE/Qwen have not passed the complete upstream-parity and
Golden Dataset gates, the optional official-Qwen-source conversion fallback is
not yet equivalence-tested, and clean-machine cold start, low-memory behavior,
upgrade rollback and uninstall still require Release CI plus Win11 hardware
evidence. Do not publish it as a verified release until `--strict-release`
passes.
