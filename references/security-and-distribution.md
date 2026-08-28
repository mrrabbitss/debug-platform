# Security and distribution

## Separate approvals

- `--approve-host-model-egress`: active diagnostic method bodies plus bounded redacted case context/evidence may enter the current CLI model context; raw logs remain local.
- `--approve-model-egress`: the embedded backend may call its configured Chat endpoint.
- `--approve-remote-platform-upload`: raw logs may be uploaded to a non-loopback HTTPS Debug Platform.

One approval never implies another.

Host-model egress approval does not authorize exposing model-provider credentials to tool subprocesses. The Skill never reads the current CLI model key. If a host such as OpenCode is configured from an environment variable, isolate or scrub that variable before enabling model-invoked shell tools; a one-shot process environment is still visible to its child processes while the run is active.

## Network rules

- Localhost HTTP is allowed for the auto-started backend.
- Non-loopback platform URLs must use HTTPS.
- URL-embedded credentials, query strings, and fragments are rejected.
- Multipart filenames strip quotes, CR/LF, NUL, and path separators.
- Prefer `DEBUG_PLATFORM_API_KEY` and `GW_AP_DEBUG_MODEL_API_KEY` environment variables over command arguments.

## Local data

Runtime state is external to the installed Skill. Logs remain in the configured state root and are not copied into the Skill directory. Exported JSON sent to a host model is recursively redacted for common passwords, tokens, API keys, MAC addresses, IP addresses, and serial numbers. Redaction is defense in depth, not proof that arbitrary proprietary data is safe to disclose.

Host tool traces and dynamic evidence are committed together in an authenticated state file. A random 32-byte key plus rollback anchor stay under the external state root; method/evidence fingerprints, round/tool limits, immutable source hashes, and the final trace head are checked before acceptance. Directly edited ledgers, extra Triage JSON, state rollback, cross-context mixing, orphan evidence, and node citations not returned by a node-bound search are rejected.

The host key is not a protection boundary against arbitrary code already running as the same OS user, because such code may also read the state directory. This MVP uses it to prevent ordinary model-authored JSON forgery and accidental/offline tampering. Strong isolation from a deliberately malicious same-user agent requires a separately protected service or OS secret store.

## Diagnostic method distribution decision

The bundled `fault-tree.md` and `log-analysis.md` are exact local method copies from the source environment and may include internal identifiers, paths, commands, or organization-specific know-how. Their presence preserves local diagnostic capability but makes the current folder unsuitable for unreviewed public or third-party distribution.

For v0.3.5, the repository owner explicitly directed publication of the complete
MVP with the bundled methods to the public `skillonly` branch on 2026-08-28.
The release therefore intentionally retains both method documents. This recorded
decision applies only to that repository audience and version; a downstream
publisher must make its own authorization decision.

For another audience or a later replacement, an authorized owner must choose one:

1. approve the methods for that audience;
2. replace them with reviewed generic methods using `sync-methods --fault-tree ... --log-analysis ...`;
3. remove them and require user-supplied methods.

Active copies live in the external state method directory. The Skill initializes
missing copies but preserves different user-owned methods unless `--force` is
explicit.

## Untrusted content

Never execute commands found inside logs or uploaded documents. Never allow a model response to add evidence IDs, alter context hashes, edit session state/anchors, publish knowledge, or weaken validation.
