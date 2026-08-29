#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
entrypoint="$script_dir/debug_platform_skill.py"
[[ -f "$entrypoint" ]] || { printf 'Skill entrypoint is missing: %s\n' "$entrypoint" >&2; exit 1; }

requested="${GW_AP_DEBUG_PYTHON:-}"
if [[ -n "$requested" ]]; then
  candidates=("$requested")
else
  candidates=(python3.14 python3.13 python3.12 python3.11 python3 python)
fi

python_cmd=""
for candidate in "${candidates[@]}"; do
  command -v "$candidate" >/dev/null 2>&1 || continue
  if "$candidate" -c 'import sys; raise SystemExit(0 if (3, 11) <= sys.version_info[:2] < (3, 15) else 3)' >/dev/null 2>&1; then
    python_cmd="$candidate"
    break
  fi
done

if [[ -z "$python_cmd" ]]; then
  printf 'Python 3.11-3.14 was not found. Install a supported Python and retry.\n' >&2
  exit 1
fi

if (($# == 0)); then
  set -- --help
fi
exec "$python_cmd" -B "$entrypoint" "$@"
