#!/bin/bash
# Deployed-config archive (task 0.3, 2026-11-18).
#
# config.yaml is gitignored (local only) but ships in the pipeline image and the Lambdas. Every
# deploy stores the exact file in the private bucket <env>-wwii-config-<account>
# (cloudformation/storage.yaml, ConfigBucket) under its deploy version, so any deployed config can
# be fetched back. One implementation for both directions: scripts/deploy_all.sh sources this file
# and calls config_archive_put; operators run it directly to list or fetch.
#
#   bash scripts/config_archive.sh list                    # deployed versions, newest last
#   bash scripts/config_archive.sh get <version|latest> [dest]
#                                                          # default dest: config.yaml.restored
#
# Never overwrites ./config.yaml itself: review the fetched file, then copy it into place.
# Requires AWS credentials (AWS_PROFILE) with access to the bucket. Environment: ENV_NAME (default
# dev), AWS_DEFAULT_REGION (default us-east-1). config.yaml can hold API keys
# (api.nara_api_key, api.noaa_api_token): the bucket is private, and pipeline roles are denied.

CONFIG_ARCHIVE_PREFIX="deployed"

config_archive_bucket() {  # <env> -> bucket name
  local env="$1" account
  account=$(aws sts get-caller-identity --query Account --output text) || {
    echo "  ✗ cannot read the AWS account (aws sts get-caller-identity failed)" >&2; return 1;
  }
  printf '%s' "${env}-wwii-config-${account}"
}

config_archive_put() {  # <env> <region> <version> <config file>: store one deployed config
  local env="$1" region="$2" version="$3" file="$4" bucket key
  [ -s "$file" ] || { echo "  ✗ $file is missing or empty - nothing to archive" >&2; return 1; }
  [[ "$version" =~ ^[0-9a-f]{40}\+cfg-[0-9a-f]{8}$ ]] || {
    echo "  ✗ refusing to archive under an unexpected version '$version'" >&2; return 1;
  }
  # the file must be the one the version names (config.yaml could change during a long deploy)
  local got
  got=$(sha256sum "$file" | cut -c1-8)
  [ "$got" = "${version##*+cfg-}" ] || {
    echo "  ✗ $file changed since the version was stamped (now cfg-$got, version says" >&2
    echo "    cfg-${version##*+cfg-}): this deploy shipped the earlier file; redeploy" >&2
    return 1
  }
  bucket=$(config_archive_bucket "$env") || return 1
  key="$CONFIG_ARCHIVE_PREFIX/$version/config.yaml"
  aws s3 cp "$file" "s3://$bucket/$key" --region "$region" --sse AES256 --only-show-errors \
    || { echo "  ✗ archiving config.yaml to s3://$bucket/$key failed" >&2; return 1; }
  printf '%s\n' "$version" | aws s3 cp - "s3://$bucket/$CONFIG_ARCHIVE_PREFIX/LATEST" \
      --region "$region" --sse AES256 --only-show-errors \
    || { echo "  ✗ updating s3://$bucket/$CONFIG_ARCHIVE_PREFIX/LATEST failed" >&2; return 1; }
  echo "  ✓ config.yaml archived: s3://$bucket/$key"
}

config_archive_list() {  # <env> <region>
  local bucket
  bucket=$(config_archive_bucket "$1") || return 1
  aws s3api list-objects-v2 --bucket "$bucket" --prefix "$CONFIG_ARCHIVE_PREFIX/" \
      --region "$2" --query "sort_by(Contents || \`[]\`, &LastModified)[?ends_with(Key, '/config.yaml')].[LastModified, Key]" \
      --output text
}

config_archive_get() {  # <env> <region> <version|latest> <dest>
  local env="$1" region="$2" version="$3" dest="$4" bucket
  bucket=$(config_archive_bucket "$env") || return 1
  if [ "$version" = "latest" ]; then
    version=$(aws s3 cp "s3://$bucket/$CONFIG_ARCHIVE_PREFIX/LATEST" - --region "$region") || {
      echo "  ✗ no LATEST marker in s3://$bucket/$CONFIG_ARCHIVE_PREFIX/" >&2; return 1;
    }
    version=$(printf '%s' "$version" | tr -d '[:space:]')
  fi
  [[ "$version" =~ ^[0-9a-f]{40}\+cfg-[0-9a-f]{8}$ ]] || {
    echo "  ✗ not a deploy version: '$version' (expected <commit>+cfg-<8 hex>)" >&2; return 1;
  }
  [ "$dest" != "config.yaml" ] && [ "$(basename "$dest")" != "config.yaml" ] || {
    echo "  ✗ will not overwrite config.yaml - fetch to another file and review it first" >&2; return 1;
  }
  (umask 077 && aws s3 cp "s3://$bucket/$CONFIG_ARCHIVE_PREFIX/$version/config.yaml" "$dest" \
      --region "$region" --only-show-errors) \
    || { echo "  ✗ fetching $version failed" >&2; return 1; }
  local want got
  want="${version##*+cfg-}"
  got=$(sha256sum "$dest" | cut -c1-8)
  [ "$want" = "$got" ] || {
    echo "  ✗ $dest does not match its version (cfg-$want, file hash $got)" >&2; return 1;
  }
  echo "  ✓ $version -> $dest (hash verified)"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  set -euo pipefail
  ENV_NAME="${ENV_NAME:-dev}"
  REGION="${AWS_DEFAULT_REGION:-us-east-1}"
  case "${1:-}" in
    list) config_archive_list "$ENV_NAME" "$REGION" ;;
    get)  config_archive_get "$ENV_NAME" "$REGION" "${2:?usage: get <version|latest> [dest]}" \
            "${3:-config.yaml.restored}" ;;
    *)    sed -n '2,18p' "$0"; exit 2 ;;
  esac
fi
