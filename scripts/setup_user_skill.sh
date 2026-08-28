#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: setup_user_skill.sh [options]

Register this checkout as a user-level Skill for Codex, Claude Code, and OpenCode.

Options:
  --clients LIST       all or comma-separated codex,claude,opencode (default: all)
  --home-root PATH     user home used for discovery paths (default: $HOME)
  --python COMMAND     Python command (default: python3)
  --update             fast-forward the checkout from origin/skillonly
  --bootstrap          create or refresh the external runtime environment
  --validate           run tests, provenance, release, and host-agent checks
  --plan               print actions without changing files or running commands
  -h, --help           show this help
EOF
}

clients="all"
home_root="${HOME}"
python_cmd="${PYTHON:-python3}"
do_update=false
do_bootstrap=false
do_validate=false
plan_only=false

while (($#)); do
  case "$1" in
    --clients) clients="${2:?missing value for --clients}"; shift 2 ;;
    --home-root) home_root="${2:?missing value for --home-root}"; shift 2 ;;
    --python) python_cmd="${2:?missing value for --python}"; shift 2 ;;
    --update) do_update=true; shift ;;
    --bootstrap) do_bootstrap=true; shift ;;
    --validate) do_validate=true; shift ;;
    --plan) plan_only=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
skill_root="$(cd "$script_dir/.." && pwd -P)"
[[ -f "$skill_root/SKILL.md" ]] || { printf 'SKILL.md is missing: %s\n' "$skill_root" >&2; exit 1; }

step() { printf '[gw-ap-debug] %s\n' "$*"; }

run_checked() {
  if $plan_only; then
    printf '[gw-ap-debug] PLAN:'
    printf ' %q' "$@"
    printf '\n'
  else
    (cd "$skill_root" && "$@")
  fi
}

resolve_link_target() {
  local target="$1" link_target
  link_target="$(readlink "$target")"
  if [[ "$link_target" = /* ]]; then
    (cd "$link_target" && pwd -P)
  else
    (cd "$(dirname "$target")/$link_target" && pwd -P)
  fi
}

if [[ "$clients" == "all" ]]; then
  requested=(codex claude opencode)
else
  IFS=',' read -r -a requested <<<"$clients"
fi

declare -A seen=()
for client in "${requested[@]}"; do
  client="${client,,}"
  [[ -n "$client" ]] || continue
  [[ -z "${seen[$client]:-}" ]] || continue
  seen[$client]=1
  case "$client" in
    codex) relative=".agents/skills/gw-ap-debug" ;;
    claude) relative=".claude/skills/gw-ap-debug" ;;
    opencode) relative=".config/opencode/skills/gw-ap-debug" ;;
    *) printf 'Unsupported client: %s\n' "$client" >&2; exit 2 ;;
  esac
  target="$home_root/$relative"
  if [[ -L "$target" ]]; then
    existing="$(resolve_link_target "$target")"
    [[ "$existing" == "$skill_root" ]] || {
      printf '%s target points elsewhere: %s -> %s\n' "$client" "$target" "$existing" >&2
      exit 1
    }
    step "$client already registered: $target"
  elif [[ -e "$target" ]]; then
    printf '%s target already exists and is not a symlink: %s\n' "$client" "$target" >&2
    exit 1
  elif $plan_only; then
    step "PLAN: create $client symlink $target -> $skill_root"
  else
    mkdir -p "$(dirname "$target")"
    ln -s "$skill_root" "$target"
    step "Registered $client: $target"
  fi
done

$do_update && run_checked git -C "$skill_root" pull --ff-only origin skillonly
$do_bootstrap && run_checked "$python_cmd" -B "$skill_root/scripts/debug_platform_skill.py" bootstrap
if $do_validate; then
  run_checked "$python_cmd" -B -m unittest discover -s tests -q
  run_checked "$python_cmd" -B "$skill_root/scripts/check_provenance.py"
  run_checked "$python_cmd" -B "$skill_root/scripts/validate_release.py"
  run_checked "$python_cmd" -B "$skill_root/scripts/debug_platform_skill.py" doctor --check host-agent
fi

step "Ready. Canonical checkout: $skill_root"
