#!/bin/bash
# Component-version preflight: catch stale vendored components before a deploy.
#
# Checks:
#   1. git submodules (e.g. openserp) — pinned commit vs the latest UPSTREAM release tag.
#   2. Dockerfile base-image pins (FROM ...@sha256:...) — pinned digest vs the current digest
#      for the same tag in the registry.
#
# Motivation: OpenSERP silently drifted 78 commits / 2 minor versions behind upstream, which
# caused empty search results AND stale-dependency CVEs. A stale base image likewise accrues
# unpatched OS/lib CVEs. This surfaces that drift at deploy time.
#
# Usage:
#   bash scripts/check_component_versions.sh            # warn-only (exit 0)
#   bash scripts/check_component_versions.sh --strict   # exit 1 if anything is behind
#
# Network: needs git fetch (submodules) + `docker/podman manifest inspect` or `skopeo`
# (base images). Gracefully skips a check it can't perform (prints a notice).

set -uo pipefail
cd "$(dirname "$0")/.."

STRICT=0
[ "${1:-}" = "--strict" ] && STRICT=1
behind=0

say_ok()   { echo "  ✓ $*"; }
say_warn() { echo "  ⚠ $*"; behind=1; }
say_skip() { echo "  — $* (skipped)"; }

echo "=== Component version check ==="

# --- 1. Submodules vs upstream latest tag ---
if [ -f .gitmodules ]; then
  # Parse "path = X" lines from .gitmodules
  while read -r path; do
    [ -z "$path" ] && continue
    [ -d "$path/.git" ] || [ -f "$path/.git" ] || { say_skip "$path (not initialized)"; continue; }
    (
      cd "$path" || exit 0
      git fetch --tags --quiet origin 2>/dev/null || { echo "  — $path (fetch failed)"; exit 0; }
      pinned="$(git rev-parse HEAD)"
      pinned_desc="$(git describe --tags --always 2>/dev/null || echo "$pinned")"
      latest_tag="$(git tag --sort=-creatordate 2>/dev/null | head -1)"
      if [ -z "$latest_tag" ]; then
        echo "  — $path (no upstream tags)"; exit 0
      fi
      latest_commit="$(git rev-list -n1 "$latest_tag" 2>/dev/null)"
      if [ "$pinned" = "$latest_commit" ]; then
        echo "  ✓ $path @ $pinned_desc (latest: $latest_tag)"
      elif git merge-base --is-ancestor "$latest_commit" HEAD 2>/dev/null; then
        # HEAD already CONTAINS the latest tag (local patches on top) -> current, not behind.
        echo "  ✓ $path @ $pinned_desc (contains latest release $latest_tag + local patches)"
      else
        n="$(git rev-list --count "HEAD..$latest_tag" 2>/dev/null || echo "?")"
        echo "  ⚠ $path @ $pinned_desc is BEHIND latest release $latest_tag ($n commits)"
        exit 7
      fi
    )
    [ "$?" = "7" ] && behind=1
  done < <(grep -E '^\s*path\s*=' .gitmodules | sed -E 's/.*=\s*//')
else
  say_skip "no .gitmodules"
fi

# --- 2. Dockerfile base-image pins vs current registry digest ---
inspect_digest() {  # $1 = image:tag  -> prints current digest or empty
  if command -v skopeo &>/dev/null; then
    skopeo inspect --format '{{.Digest}}' "docker://$1" 2>/dev/null && return 0
  fi
  local bin=docker; command -v docker &>/dev/null || bin=podman
  "$bin" manifest inspect "$1" 2>/dev/null | grep -oE 'sha256:[a-f0-9]{64}' | head -1
}

for df in Dockerfile Dockerfile.* openserp/Dockerfile; do
  [ -f "$df" ] || continue
  while read -r line; do
    # Extract "image:tag" and the pinned digest from: FROM [--platform=..] image:tag@sha256:...
    ref="$(echo "$line" | grep -oE '[a-z0-9./_-]+:[a-zA-Z0-9._-]+@sha256:[a-f0-9]{64}' | head -1)"
    [ -z "$ref" ] && continue
    image_tag="${ref%@*}"
    pinned_digest="${ref#*@}"
    current="$(inspect_digest "$image_tag")"
    if [ -z "$current" ]; then
      echo "  — $df: $image_tag (digest lookup unavailable)"
      continue
    fi
    if [ "$current" = "$pinned_digest" ]; then
      echo "  ✓ $df: $image_tag @ current digest"
    else
      echo "  ⚠ $df: $image_tag pin is STALE (pinned ${pinned_digest:0:19}… vs current ${current:0:19}…)"
      behind=1
    fi
  done < <(grep -nE '^FROM .*@sha256:' "$df" 2>/dev/null)
done

echo "==============================="
if [ "$behind" = "1" ]; then
  if [ "$STRICT" = "1" ]; then
    echo "COMPONENT CHECK: FAIL (components behind; --strict)"
    exit 1
  fi
  echo "COMPONENT CHECK: WARN (components behind — review before deploy)"
else
  echo "COMPONENT CHECK: OK (all components current)"
fi
exit 0
