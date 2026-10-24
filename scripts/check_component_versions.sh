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
#   COMPONENT_UPGRADE_PROMPT=1 bash scripts/check_component_versions.sh   # interactively offer to
#     upgrade a stale submodule to its latest release tag (TTY only; Enter = skip; never auto-pulls;
#     stages the new gitlink for you to review + commit, then rebuild/push the image). Non-interactive
#     runs (CI) ignore the prompt and stay warn-only, so this never blocks a pipeline.
#
# Network: needs git fetch (submodules) + `docker/podman manifest inspect` or `skopeo`
# (base images). Gracefully skips a check it can't perform (prints a notice).

set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

STRICT=0
[ "${1:-}" = "--strict" ] && STRICT=1
behind=0

say_ok()   { echo "  ✓ $*"; }
say_warn() { echo "  ⚠ $*"; behind=1; }
say_skip() { echo "  — $* (skipped)"; }

# Optional interactive upgrade of a stale submodule. OFF unless COMPONENT_UPGRADE_PROMPT=1, and it
# only ever prompts on a real TTY — so CI / non-interactive runs NEVER hang and stay warn-only.
# Default answer is NO (Enter = skip). On "y" it checks out the target tag in the submodule and
# STAGES the new gitlink in the superproject; the user still reviews/commits + rebuilds the image.
# Never auto-pulls: an unreviewed upstream bump is the exact failure this guard exists to prevent.
_maybe_prompt_upgrade() {
  local path="$1" tag="$2"
  [ "${COMPONENT_UPGRADE_PROMPT:-0}" = "1" ] || return 0
  [ -t 0 ] || { echo "    (non-interactive; skipping upgrade prompt for $path)"; return 0; }
  printf "    Upgrade %s to %s now? [y/N] " "$path" "$tag"
  local ans; read -r ans
  case "$ans" in
    y | Y | yes | YES)
      if git -C "$path" checkout --quiet "$tag" 2>/dev/null; then
        git add "$path" 2>/dev/null || true
        echo "    ✓ $path checked out at $tag and staged. Review, commit the gitlink, then rebuild/push the image."
      else
        echo "    ✗ checkout of $tag failed in $path — upgrade manually (git -C $path checkout $tag)."
      fi
      ;;
    *)
      echo "    → left $path unchanged (upgrade later: git -C $path checkout $tag && git add $path)."
      ;;
  esac
}

echo "=== Component version check ==="

# --- 1. Submodules vs upstream latest tag ---
if [ -f .gitmodules ]; then
  # Parse "path = X" lines from .gitmodules
  while read -r path; do
    [ -z "$path" ] && continue
    [ -d "$path/.git" ] || [ -f "$path/.git" ] || { say_skip "$path (not initialized)"; continue; }
    latest_tag_out="$(
      cd "$path" || exit 0
      git fetch --tags --quiet origin 2>/dev/null || { echo "  — $path (fetch failed)" >&2; exit 0; }
      pinned="$(git rev-parse HEAD)"
      pinned_desc="$(git describe --tags --always 2>/dev/null || echo "$pinned")"
      latest_tag="$(git tag --sort=-creatordate 2>/dev/null | head -1)"
      if [ -z "$latest_tag" ]; then
        echo "  — $path (no upstream tags)" >&2; exit 0
      fi
      latest_commit="$(git rev-list -n1 "$latest_tag" 2>/dev/null)"
      if [ "$pinned" = "$latest_commit" ]; then
        echo "  ✓ $path @ $pinned_desc (latest: $latest_tag)" >&2
      elif git merge-base --is-ancestor "$latest_commit" HEAD 2>/dev/null; then
        # HEAD already CONTAINS the latest tag (local patches on top) -> current, not behind.
        echo "  ✓ $path @ $pinned_desc (contains latest release $latest_tag + local patches)" >&2
      else
        n="$(git rev-list --count "HEAD..$latest_tag" 2>/dev/null || echo "?")"
        echo "  ⚠ $path @ $pinned_desc is BEHIND latest release $latest_tag ($n commits)" >&2
        echo "$latest_tag"  # stdout: signal 'behind' + the target tag to the parent
      fi
    )"
    if [ -n "$latest_tag_out" ]; then
      behind=1
      _maybe_prompt_upgrade "$path" "$latest_tag_out"
    fi
  done < <(grep -E '^\s*path\s*=' .gitmodules | sed -E 's/.*=\s*//')
else
  say_skip "no .gitmodules"
fi

# --- 2. Dockerfile base-image pins vs current registry digest ---
inspect_digest() {  # $1 = image:tag  -> prints the PULLABLE manifest-list digest, or empty
  # A `FROM image@sha256:...` pin uses the registry manifest-list (RepoDigest) digest — NOT a
  # per-arch sub-manifest. skopeo gives it directly; otherwise pull + read RepoDigests (the
  # pull is why this preflight needs registry access).
  if command -v skopeo &>/dev/null; then
    skopeo inspect --format '{{.Digest}}' "docker://$1" 2>/dev/null | grep -oE 'sha256:[a-f0-9]{64}' | head -1 && return 0
  fi
  local bin=docker; command -v docker &>/dev/null || bin=podman
  "$bin" pull "$1" >/dev/null 2>&1 || return 0
  "$bin" inspect "$1" --format '{{index .RepoDigests 0}}' 2>/dev/null | grep -oE 'sha256:[a-f0-9]{64}' | head -1
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

# --- S3 validation-backstop drift: deployed Lambda must run the CURRENT validation code ----------
# The backstop Lambda MUST validate with the repo's live _validate_entity. Compare the repo-HEAD
# fingerprint (schemas + file_lock + validation_stats) to the fingerprint stamped on the deployed
# Lambda; a mismatch means a partial deploy left the backstop behind the pipeline's schemas/guard.
echo "Checking: S3 validation-backstop code drift"
repo_fp="$(bash scripts/validation_fingerprint.sh 2>/dev/null || echo '')"
env_name="${ENV_NAME:-dev}"
region="${AWS_DEFAULT_REGION:-us-east-1}"
deployed_fp="$(aws lambda get-function-configuration \
  --function-name "${env_name}-wwii-s3-validation-backstop" --region "$region" \
  --query 'Environment.Variables.VALIDATION_FINGERPRINT' --output text 2>/dev/null || echo '')"
if [ -z "$deployed_fp" ] || [ "$deployed_fp" = "None" ]; then
  say_skip "validation-backstop (not deployed / no fingerprint yet)"
elif [ "$repo_fp" = "$deployed_fp" ]; then
  say_ok "validation-backstop runs current validation code (${repo_fp:0:12}…)"
else
  say_warn "validation-backstop is STALE (repo ${repo_fp:0:12}… vs deployed ${deployed_fp:0:12}…) — run update_lambdas.sh"
fi

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
