# Image pinning for the stacks (Group 0 HIGH): task defs and job defs name an image by DIGEST,
# never by the mutable `:latest`. With `:latest` every task start pulled whatever was pushed last,
# so "which commit is live" was unanswerable (the 2026-10-28 E2E ran a stale image unseen).
#
# Sourced by scripts/deploy_all.sh and .github/workflows/deploy.yml; tests/test_deploy_image_pin.py
# runs it against a fake `aws`. Needs REGION, ECR_REPO (the registry host) and DEPLOY_TAG.
#
# Tags, and why the lifecycle policy (ecr_lifecycle_policy) can never delete a live image:
#   deploy-<UTC>-<sha12>  pushed with each build, unique to one run, so the pin is the image THIS
#                         run built and scanned (a concurrent push can move :latest, not this tag).
#                         An image a run pins without building (openserp, say) is NOT tagged: it
#                         is usually the live one, and a deploy- tag left on it by a failed run
#                         would let rule 1 expire it. It keeps :latest until a rebuild, then rule 2
#                         gives it 14 days. Rule 1 keeps the newest DEPLOY_KEEP per repo: in-flight or failed
#                         deploys only (the chandra image is ~17 GB, so the count stays small).
#   live-<env>            moved onto each pinned image once the stack is verified to run it
#                         (mark_live), and that image's deploy- tags are removed, so no rule
#                         selects it: a failed deploy or another env's deploys cannot expire it.
#   untagged              rule 2, 14 days after push: a superseded live image, once live-<env> and
#                         latest have moved off it.
# CI (deploy.yml) does not set the policy (its role has no ecr:PutLifecyclePolicy): run
# scripts/deploy_all.sh once per repo first. Two deploys of one env must not overlap (the later
# mark_live wins; the other run's image keeps no live tag).
IMAGE_DIGEST_RE='^sha256:[0-9a-f]{64}$'
DEPLOY_KEEP=2
BUILT_REPOS="${BUILT_REPOS:-}"  # repos this run pushed under DEPLOY_TAG (note_built)

note_built() { BUILT_REPOS="$BUILT_REPOS $1 "; }  # $1 = repo

ecr_lifecycle_policy() {
  printf '{"rules":[{"rulePriority":1,"description":"Keep the newest %s deploy- images (in-flight or failed deploys; the live image carries live-<env> instead, scripts/image_pin.sh)","selection":{"tagStatus":"tagged","tagPrefixList":["deploy-"],"countType":"imageCountMoreThan","countNumber":%s},"action":{"type":"expire"}},{"rulePriority":2,"description":"Expire untagged images 14 days after push. Tagged images (latest, deploy-, live-) are never selected here.","selection":{"tagStatus":"untagged","countType":"sinceImagePushed","countUnit":"days","countNumber":14},"action":{"type":"expire"}}]}' \
    "$DEPLOY_KEEP" "$DEPLOY_KEEP"
}

ensure_lifecycle() {  # $1 = repo. The deploy owns each repo's policy (replaces a hand-set one).
  local err
  if ! err=$(aws ecr put-lifecycle-policy --repository-name "$1" --region "$REGION" \
      --lifecycle-policy-text "$(ecr_lifecycle_policy)" 2>&1 >/dev/null); then
    echo "  ✗ could not set the $1 lifecycle policy: $err - check ECR access (ecr:PutLifecyclePolicy). Hard stop." >&2
    return 1
  fi
}

image_digest() {  # $1 = repo, $2 = tag; prints the digest
  local out
  if ! out=$(aws ecr describe-images --repository-name "$1" --region "$REGION" \
      --image-ids "imageTag=$2" --query 'imageDetails[0].imageDigest' --output text 2>&1); then
    echo "  ✗ $1:$2 not readable in ECR ($REGION): $out - check the push and ECR access. Hard stop." >&2
    return 1
  fi
  if ! [[ "$out" =~ $IMAGE_DIGEST_RE ]]; then
    echo "  ✗ $1:$2 has no valid digest in ECR ($REGION): got '$out'. Hard stop." >&2
    return 1
  fi
  echo "$out"
}

image_tags() {  # $1 = repo, $2 = digest; prints the tags, one per line
  local out
  if ! out=$(aws ecr describe-images --repository-name "$1" --region "$REGION" \
      --image-ids "imageDigest=$2" --query 'imageDetails[0].imageTags' --output text 2>&1); then
    echo "  ✗ could not read the tags of $1@$2: $out. Hard stop." >&2
    return 1
  fi
  [ "$out" = "None" ] || tr '\t ' '\n\n' <<<"$out"
}

ecr_add_tag() {  # $1 = repo, $2 = digest, $3 = tag (moved here if another image has it)
  local repo="$1" digest="$2" tag="$3" tags manifest media err
  tags=$(image_tags "$repo" "$digest") || return 1
  if grep -qxF -- "$tag" <<<"$tags"; then return 0; fi
  manifest=$(aws ecr batch-get-image --repository-name "$repo" --region "$REGION" \
    --image-ids "imageDigest=$digest" --query 'images[0].imageManifest' --output text 2>&1) || manifest=""
  media=$(aws ecr batch-get-image --repository-name "$repo" --region "$REGION" \
    --image-ids "imageDigest=$digest" --query 'images[0].imageManifestMediaType' --output text 2>&1) || media=""
  if [ -z "$manifest" ] || [ "$manifest" = "None" ] || [[ "$media" != application/* ]]; then
    echo "  ✗ could not read the manifest of $repo@$digest (media type '$media') - check ecr:BatchGetImage. Hard stop." >&2
    return 1
  fi
  if ! err=$(aws ecr put-image --repository-name "$repo" --region "$REGION" --image-tag "$tag" \
      --image-digest "$digest" --image-manifest "$manifest" \
      --image-manifest-media-type "$media" 2>&1 >/dev/null); then
    echo "  ✗ could not tag $repo@$digest as $tag: $err - check ecr:PutImage. Hard stop." >&2
    return 1
  fi
  echo "  tagged $repo@$digest as $tag" >&2
}

drop_tag() {  # $1 = repo, $2 = tag. Only for an image that keeps another tag (or it is deleted).
  local out
  if ! out=$(aws ecr batch-delete-image --repository-name "$1" --region "$REGION" \
      --image-ids "imageTag=$2" --query 'length(failures)' --output text 2>&1) || [ "$out" != "0" ]; then
    echo "  ✗ could not drop tag $2 from live $1: $out - rule 1 may expire the live image;" >&2
    echo "    remove the tag by hand (aws ecr batch-delete-image --repository-name $1" >&2
    echo "    --image-ids imageTag=$2). Hard stop." >&2
    return 1
  fi
}

pin_image() {  # $1 = repo; prints <registry>/<repo>@<digest>. Messages go to stderr.
  local repo="$1" digest tags
  if [[ "$BUILT_REPOS" == *" $repo "* ]]; then
    digest=$(image_digest "$repo" "$DEPLOY_TAG") || return 1
    echo "  pinned $repo -> $digest (built and scanned by this run, $DEPLOY_TAG)" >&2
    # a reproducible build can equal the live image: it must not keep a deploy- tag past this
    # point (a failed run would leave it there for rule 1); its live-<env> tag keeps it
    tags=$(image_tags "$repo" "$digest") || return 1
    if grep -q '^live-' <<<"$tags"; then
      drop_tag "$repo" "$DEPLOY_TAG" || return 1
      echo "  $repo@$digest is already live: dropped $DEPLOY_TAG" >&2
    fi
  else
    digest=$(image_digest "$repo" latest) || return 1
    echo "  pinned $repo -> $digest (not built by this run: its current :latest)" >&2
  fi
  echo "$ECR_REPO/$repo@$digest"
}

verify_stack_images() {  # $1 = stack, then ParamKey=<pinned uri> ...: hard stop on any mismatch
  local stack="$1" pair key want got status bad=0
  shift
  if ! status=$(aws cloudformation describe-stacks --stack-name "$stack" --region "$REGION" \
      --query 'Stacks[0].StackStatus' --output text 2>&1); then
    echo "  ✗ could not read $stack: $status - check CloudFormation access. Hard stop." >&2
    return 1
  fi
  # UPDATE_COMPLETE_CLEANUP_IN_PROGRESS: the update is committed (it can no longer roll back),
  # only old resources are being removed; deploy_aws.py returns in it (seen 2026-11-25)
  case "$status" in CREATE_COMPLETE | UPDATE_COMPLETE | UPDATE_COMPLETE_CLEANUP_IN_PROGRESS) ;; *)
    echo "  ✗ $stack is $status, not settled (CREATE_COMPLETE, UPDATE_COMPLETE or" >&2
    echo "    UPDATE_COMPLETE_CLEANUP_IN_PROGRESS; a rollback state means the old images are back);" >&2
    echo "    check the stack events (aws cloudformation describe-stack-events --stack-name $stack). Hard stop." >&2
    return 1 ;;
  esac
  for pair in "$@"; do
    key="${pair%%=*}"
    want="${pair#*=}"
    if ! got=$(aws cloudformation describe-stacks --stack-name "$stack" --region "$REGION" \
        --query "Stacks[0].Parameters[?ParameterKey=='$key'].ParameterValue" --output text 2>&1); then
      echo "  ✗ could not read $stack parameters: $got - check CloudFormation access. Hard stop." >&2
      return 1
    fi
    if [ "$got" != "$want" ]; then
      echo "  ✗ $stack $key is '$got', not the pinned $want - check the stack events" >&2
      echo "    (aws cloudformation describe-stack-events --stack-name $stack). Hard stop." >&2
      bad=1
    else
      echo "  ✓ $stack $key pinned: $got" >&2
    fi
  done
  return "$bad"
}

mark_live() {  # $1 = env, then pinned uris: tag each live-<env>, then drop its deploy- tags
  local env="$1" uri repo digest t tags
  shift
  for uri in "$@"; do
    repo="${uri#*/}"
    repo="${repo%@*}"
    digest="${uri##*@}"
    # live-<env> first: removing an image's last tag would delete the image
    ecr_add_tag "$repo" "$digest" "live-$env" || return 1
    tags=$(image_tags "$repo" "$digest") || return 1
    for t in $(grep '^deploy-' <<<"$tags" || true); do
      drop_tag "$repo" "$t" || return 1
    done
    echo "  ✓ $repo@$digest is live-$env" >&2
  done
}
