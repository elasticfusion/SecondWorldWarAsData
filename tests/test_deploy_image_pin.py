"""Guard: the stacks get every image by digest, never by the mutable `:latest` (Group 0 HIGH).

scripts/image_pin.sh is sourced and run against a fake, stateful `aws` (an ECR of
repo -> {digest: [tags]} plus stack parameters), so tag moves and tag deletions are real:
- a built image is pinned through this run's unique deploy- tag, so a concurrent push that moves
  :latest cannot change what is pinned;
- an image the run did not build is pinned from :latest and NOT tagged (a deploy- tag left on a
  live image by a failed run would let rule 1 expire it);
- after the stack is verified, mark_live moves live-<env> onto each pin and drops its deploy- tags,
  so neither lifecycle rule can select a live image.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "image_pin.sh"
DEPLOY = (ROOT / "scripts" / "deploy_all.sh").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
REGISTRY = "123.dkr.ecr.us-east-1.amazonaws.com"
TAG = "deploy-20261125T000000Z-0123456789ab"
OLD, NEW, RACE = ("sha256:" + c * 64 for c in "abc")
MEDIA = "application/vnd.oci.image.manifest.v1+json"

FAKE_AWS = r"""#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
path = os.environ["FAKE_STATE"]
st = json.load(open(path))
st["calls"].append(args)
def opt(n):
    return args[args.index(n) + 1] if n in args else ""
def save():
    json.dump(st, open(path, "w"))
def fail(msg, rc=254):
    save(); sys.stderr.write(msg + "\n"); sys.exit(rc)
cmd = " ".join(args[:2])
if cmd in st.get("fail", {}):
    fail(st["fail"][cmd])
nth = st.setdefault("n", {})
nth[cmd] = nth.get(cmd, 0) + 1
if st.get("fail_nth", {}).get(cmd) == nth[cmd]:
    fail("ThrottlingException")
repo = st["repos"].get(opt("--repository-name"), {})
def find(ids):
    kind, val = ids.split("=", 1)
    for d, tags in repo.items():
        if (kind == "imageTag" and val in tags) or (kind == "imageDigest" and val == d):
            return d
    fail("ImageNotFoundException: " + ids)
if cmd == "ecr describe-images":
    d = find(opt("--image-ids"))
    if "imageDigest" in opt("--query"):
        print(d)
    else:
        print("\t".join(repo[d]) or "None")
elif cmd == "ecr batch-get-image":
    find(opt("--image-ids"))
    print(st.get("media", "application/vnd.oci.image.manifest.v1+json")
          if "MediaType" in opt("--query") else '{"schemaVersion":2}')
elif cmd == "ecr put-image":
    tag, d = opt("--image-tag"), opt("--image-digest")
    if tag in repo[d]:
        fail("ImageAlreadyExistsException")
    for tags in repo.values():
        if tag in tags:
            tags.remove(tag)
    repo[d].append(tag)
elif cmd == "ecr batch-delete-image":
    tag = opt("--image-ids").split("=", 1)[1]
    for d, tags in list(repo.items()):
        if tag in tags:
            tags.remove(tag)
            if not tags:
                del repo[d]
    print(st.get("delete_failures", 0))
elif cmd == "ecr put-lifecycle-policy":
    st["policy"][opt("--repository-name")] = opt("--lifecycle-policy-text")
elif cmd == "cloudformation describe-stacks" and "StackStatus" in opt("--query"):
    print(st.get("status", "UPDATE_COMPLETE"))
elif cmd == "cloudformation describe-stacks":
    key = opt("--query").split("'")[1]
    print(st["params"].get(opt("--stack-name"), {}).get(key, "None"))
else:
    fail("unexpected aws call " + cmd, 99)
save()
"""


@pytest.fixture
def ecr(tmp_path):
    """Run bash with the helper sourced; returns (result, state after)."""

    def run(tail: str, repos: dict, built: str = "", **extra):
        bindir = tmp_path / "bin"
        bindir.mkdir(exist_ok=True)
        aws = bindir / "aws"
        aws.write_text(FAKE_AWS)
        aws.chmod(0o755)
        state = tmp_path / "state.json"
        st = {"repos": repos, "calls": [], "policy": {}, "params": {}, **extra}
        state.write_text(json.dumps(st))
        script = tmp_path / "run.sh"
        script.write_text(
            f"set -e\nREGION=us-east-1\nECR_REPO={REGISTRY}\nDEPLOY_TAG={TAG}\n"
            f"source {HELPER}\n{built}\n{tail}\n"
        )
        env = {"PATH": f"{bindir}:{os.environ['PATH']}", "FAKE_STATE": str(state)}
        r = subprocess.run(
            ["bash", str(script)], capture_output=True, text=True, env=env, check=False
        )
        return r, json.loads(state.read_text())

    return run


def _cmds(st, cmd):
    return [c for c in st["calls"] if c[:2] == cmd.split()]


def test_a_built_image_is_pinned_through_this_runs_tag(ecr):
    # a concurrent push moved :latest to RACE after this run pushed NEW under its own tag
    repos = {"wwii-pipeline": {NEW: [TAG], RACE: ["latest"]}}
    r, st = ecr(
        'X=$(pin_image wwii-pipeline) || exit 1; printf "%s" "$X"',
        repos,
        built="note_built wwii-pipeline",
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout == f"{REGISTRY}/wwii-pipeline@{NEW}"
    assert "built and scanned by this run" in r.stderr
    assert not _cmds(st, "ecr put-image")


def test_an_image_not_built_is_pinned_from_latest_and_not_tagged(ecr):
    repos = {"wwii-openserp": {OLD: ["latest", "live-dev"]}}
    r, st = ecr('X=$(pin_image wwii-openserp) || exit 1; printf "%s" "$X"', repos)
    assert r.returncode == 0, r.stderr
    assert r.stdout == f"{REGISTRY}/wwii-openserp@{OLD}"
    assert "not built by this run" in r.stderr
    # the live image must never carry a deploy- tag (rule 1 could expire it)
    assert st["repos"]["wwii-openserp"][OLD] == ["latest", "live-dev"]
    assert not _cmds(st, "ecr put-image")


def test_mark_live_again_changes_nothing(ecr):
    repos = {"wwii-openserp": {OLD: ["latest", "live-dev"]}}
    r, st = ecr(f"mark_live dev {REGISTRY}/wwii-openserp@{OLD}", repos)
    assert r.returncode == 0, r.stderr
    assert st["repos"]["wwii-openserp"] == {OLD: ["latest", "live-dev"]}
    assert not _cmds(st, "ecr put-image") and not _cmds(st, "ecr batch-delete-image")


def test_mark_live_tags_with_the_images_own_manifest_type(ecr):
    r, st = ecr(f"mark_live dev {REGISTRY}/wwii-x@{NEW}", {"wwii-x": {NEW: [TAG]}})
    assert r.returncode == 0, r.stderr
    (put,) = _cmds(st, "ecr put-image")
    assert put[put.index("--image-manifest-media-type") + 1] == MEDIA


def test_mark_live_stops_when_the_tags_cannot_be_read(ecr):
    """The second tag read (for the deploy- tags to drop) fails: no silent "live" success."""
    r, _ = ecr(
        f"mark_live dev {REGISTRY}/wwii-x@{NEW} || exit 7",
        {"wwii-x": {NEW: ["latest", TAG]}},
        fail_nth={"ecr describe-images": 2},
    )
    assert r.returncode == 7
    assert "could not read the tags" in r.stderr and "ThrottlingException" in r.stderr
    assert "is live-dev" not in r.stderr


def test_a_build_equal_to_the_live_image_drops_its_run_tag_at_once(ecr):
    """A reproducible build pushes the live digest again: its deploy- tag must not stay on the live
    image until mark_live (a failed run would leave it there for rule 1)."""
    repos = {"wwii-pipeline": {OLD: ["latest", "live-dev", TAG]}}
    r, st = ecr(
        'X=$(pin_image wwii-pipeline) || exit 1; printf "%s" "$X"',
        repos,
        built="note_built wwii-pipeline",
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout == f"{REGISTRY}/wwii-pipeline@{OLD}"
    assert st["repos"]["wwii-pipeline"] == {OLD: ["latest", "live-dev"]}
    assert "already live" in r.stderr


def test_a_failed_drop_on_a_live_build_is_a_hard_stop(ecr):
    repos = {"wwii-pipeline": {OLD: ["live-dev", TAG]}}
    r, _ = ecr(
        "X=$(pin_image wwii-pipeline) || exit 7",
        repos,
        built="note_built wwii-pipeline",
        delete_failures=1,
    )
    assert r.returncode == 7
    assert "remove the tag by hand" in r.stderr


def test_a_built_repo_without_its_run_tag_is_a_hard_stop(ecr):
    """note_built says this run pushed it: falling back to :latest would pin someone else's push."""
    repos = {"wwii-pipeline": {RACE: ["latest"]}}
    r, _ = ecr(
        "X=$(pin_image wwii-pipeline) || exit 7",
        repos,
        built="note_built wwii-pipeline",
    )
    assert r.returncode == 7
    assert f"wwii-pipeline:{TAG} not readable" in r.stderr
    assert "ImageNotFoundException" in r.stderr  # the AWS error is shown, not hidden


@pytest.mark.parametrize("digest", ["None", "sha256:short", "latest"])
def test_an_invalid_digest_is_a_hard_stop(ecr, digest):
    r, st = ecr("X=$(pin_image wwii-x) || exit 7", {"wwii-x": {digest: ["latest"]}})
    assert r.returncode == 7
    assert "has no valid digest" in r.stderr
    assert not _cmds(st, "ecr put-image")


def test_a_failed_tag_is_a_hard_stop_with_the_aws_error(ecr):
    r, _ = ecr(
        f"mark_live dev {REGISTRY}/wwii-x@{OLD} || exit 7",
        {"wwii-x": {OLD: ["latest"]}},
        fail={"ecr put-image": "AccessDeniedException: no ecr:PutImage"},
    )
    assert r.returncode == 7
    assert "could not tag" in r.stderr and "AccessDeniedException" in r.stderr


def test_an_unreadable_manifest_is_a_hard_stop(ecr):
    r, st = ecr(
        f"mark_live dev {REGISTRY}/wwii-x@{OLD} || exit 7",
        {"wwii-x": {OLD: ["latest"]}},
        media="None",
    )
    assert r.returncode == 7
    assert "could not read the manifest" in r.stderr
    assert not _cmds(st, "ecr put-image")


def test_the_lifecycle_policy_sent_is_the_policy(ecr):
    r, st = ecr("ensure_lifecycle wwii-x; ecr_lifecycle_policy", {"wwii-x": {}})
    assert r.returncode == 0, r.stderr
    assert st["policy"]["wwii-x"] == r.stdout
    keep, untagged = sorted(
        json.loads(r.stdout)["rules"], key=lambda x: x["rulePriority"]
    )
    assert keep["selection"] == {
        "tagStatus": "tagged",
        "tagPrefixList": ["deploy-"],
        "countType": "imageCountMoreThan",
        "countNumber": 2,
    }
    assert untagged["selection"] == {
        "tagStatus": "untagged",
        "countType": "sinceImagePushed",
        "countUnit": "days",
        "countNumber": 14,
    }
    # no rule may select live-<env>: rule 1 is deploy- only, rule 2 untagged only
    assert all(x["action"] == {"type": "expire"} for x in (keep, untagged))


def test_a_failed_lifecycle_policy_is_a_hard_stop(ecr):
    r, _ = ecr(
        "ensure_lifecycle wwii-x || exit 7",
        {"wwii-x": {}},
        fail={"ecr put-lifecycle-policy": "AccessDeniedException"},
    )
    assert r.returncode == 7
    assert "lifecycle policy" in r.stderr and "AccessDeniedException" in r.stderr


def test_mark_live_moves_live_and_drops_deploy_tags(ecr):
    older = "deploy-20261101T000000Z-aaaaaaaaaaaa"
    repos = {"wwii-pipeline": {OLD: ["live-dev"], NEW: ["latest", older, TAG]}}
    r, st = ecr(f"mark_live dev {REGISTRY}/wwii-pipeline@{NEW}", repos)
    assert r.returncode == 0, r.stderr
    # the old live image is now untagged (rule 2 expires it); the new one keeps latest + live-dev
    assert st["repos"]["wwii-pipeline"] == {OLD: [], NEW: ["latest", "live-dev"]}
    # live-dev was put BEFORE any deploy- tag was removed (removing the last tag deletes the image)
    kinds = [c[1] for c in st["calls"] if c[1] in ("put-image", "batch-delete-image")]
    assert kinds == ["put-image", "batch-delete-image", "batch-delete-image"]


def test_mark_live_on_an_image_with_only_a_deploy_tag_keeps_the_image(ecr):
    repos = {"wwii-openserp": {OLD: [TAG]}}
    r, st = ecr(f"mark_live dev {REGISTRY}/wwii-openserp@{OLD}", repos)
    assert r.returncode == 0, r.stderr
    assert st["repos"]["wwii-openserp"] == {OLD: ["live-dev"]}


def test_a_failed_tag_drop_is_a_hard_stop(ecr):
    repos = {"wwii-x": {NEW: [TAG]}}
    r, _ = ecr(
        f"mark_live dev {REGISTRY}/wwii-x@{NEW} || exit 7", repos, delete_failures=1
    )
    assert r.returncode == 7
    assert (
        f"could not drop tag {TAG}" in r.stderr and "remove the tag by hand" in r.stderr
    )


def test_verify_passes_when_the_stack_runs_every_pin(ecr):
    pins = {
        "PipelineImageUri": f"{REGISTRY}/wwii-pipeline@{NEW}",
        "AvImageUri": f"{REGISTRY}/wwii-clamav@{OLD}",
    }
    args = " ".join(f'"{k}={v}"' for k, v in pins.items())
    r, _ = ecr(f"verify_stack_images s {args}", {}, params={"s": pins})
    assert r.returncode == 0, r.stderr
    assert r.stderr.count("pinned:") == 2


def test_verify_names_every_mismatch(ecr):
    want = {
        "PipelineImageUri": f"{REGISTRY}/wwii-pipeline@{NEW}",
        "AvImageUri": f"{REGISTRY}/wwii-clamav@{NEW}",
    }
    live = {
        "PipelineImageUri": f"{REGISTRY}/wwii-pipeline@{OLD}",
        "AvImageUri": f"{REGISTRY}/wwii-clamav:latest",
    }
    args = " ".join(f'"{k}={v}"' for k, v in want.items())
    r, _ = ecr(f"verify_stack_images s {args} || exit 7", {}, params={"s": live})
    assert r.returncode == 7
    assert "PipelineImageUri is" in r.stderr and "AvImageUri is" in r.stderr
    assert "describe-stack-events" in r.stderr


@pytest.mark.parametrize(
    "status", ["UPDATE_IN_PROGRESS", "UPDATE_ROLLBACK_COMPLETE", "ROLLBACK_COMPLETE"]
)
def test_verify_needs_a_settled_stack(ecr, status):
    uri = f"{REGISTRY}/wwii-clamav@{OLD}"
    r, _ = ecr(
        f'verify_stack_images s "AvImageUri={uri}" || exit 7',
        {},
        params={"s": {"AvImageUri": uri}},
        status=status,
    )
    assert r.returncode == 7
    assert f"s is {status}" in r.stderr and "describe-stack-events" in r.stderr


def test_verify_fails_loudly_when_the_stack_is_unreadable(ecr):
    r, _ = ecr(
        'verify_stack_images s "AvImageUri=x" || exit 7',
        {},
        fail={"cloudformation describe-stacks": "AccessDenied"},
    )
    assert r.returncode == 7
    assert "could not read s" in r.stderr and "AccessDenied" in r.stderr


def test_deploy_all_pins_every_stack_image():
    """Each pin comes after the last :latest assignment and before the stack call, and the verify
    + mark_live steps cover every pinned parameter."""
    deploy = DEPLOY.index("python3 scripts/deploy_aws.py deploy")
    for var, repo in [
        ("PIPELINE_IMAGE", "wwii-pipeline"),
        ("OPENSERP_IMAGE", "wwii-openserp"),
        ("VIDEO_IMAGE", "wwii-video"),
        ("AV_IMAGE", "wwii-clamav"),
    ]:
        pin = DEPLOY.index(f"{var}=$(pin {repo}) || exit 1")
        assert (
            DEPLOY.rindex(f'{var}="$ECR_REPO/{repo}:latest"', 0, deploy) < pin < deploy
        )
    verify = DEPLOY.index('verify_stack_images "wwii-pipeline-$ENV"')
    # right after the stack update: a later step's hard stop must not leave the pins unprotected
    assert deploy < verify < DEPLOY.index("=== 5b. Archiving")
    for key in ("PipelineImageUri", "OpenSerpImageUri", "AvImageUri", "VideoImageUri"):
        assert f'"{key}=$' in DEPLOY[deploy:verify]
    assert DEPLOY.index('mark_live "$ENV"', verify) > verify
    ocr = DEPLOY.index("aws cloudformation $ACTION")
    for var, repo in [
        ("CHANDRA_IMAGE", "wwii-chandra"),
        ("PADDLE_IMAGE", "wwii-paddle"),
    ]:
        assert DEPLOY.index(f"{var}=$(pin {repo}) || exit 1") < ocr
    assert (
        ocr
        < DEPLOY.index('verify_stack_images "$STACK_NAME"')
        < DEPLOY.index("=== OCR Deploy Complete")
    )


def test_every_push_also_pushes_the_run_tag():
    assert (
        DEPLOY.count('note_built "$repo"') == 1
        and DEPLOY.count("note_built wwii-pipeline") == 1
    )
    # tag + push, in build_scan_push and in the no-Trivy fallback
    assert DEPLOY.count('"${image%:*}:$DEPLOY_TAG"') == 2
    assert DEPLOY.count('"${PIPELINE_IMAGE%:*}:$DEPLOY_TAG"') == 2


def test_the_ci_deploy_pins_through_the_helper():
    assert WORKFLOW.count("source scripts/image_pin.sh") == 2
    assert 'IMAGE_URI=$(pin_image "${{ env.ECR_REPO }}")' in WORKFLOW
    assert "AV_IMAGE_URI=$(pin_image wwii-clamav)" in WORKFLOW
    assert 'mark_live "${{ env.ENV_NAME }}"' in WORKFLOW
    assert "/wwii-clamav:latest" not in WORKFLOW


def test_the_ci_role_can_run_the_helper():
    iam = (ROOT / "cloudformation" / "iam.yaml").read_text(encoding="utf-8")
    push = iam[iam.index("Sid: ECRPush") : iam.index("Sid: S3Deploy")]
    for action in ("DescribeImages", "BatchGetImage", "PutImage", "BatchDeleteImage"):
        assert f"ecr:{action}" in push
    assert (
        "PutLifecyclePolicy" not in iam
    )  # the operator's deploy owns the policy, not CI
