"""Guard: every deploy archives its exact config.yaml in a private bucket (task 0.3, 2026-11-18).

scripts/config_archive.sh is exercised with a fake `aws` on PATH that stores objects in a scratch
dir (no AWS call). The bucket's privacy settings are checked in cloudformation/storage.yaml.
"""

import hashlib
import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "scripts" / "config_archive.sh"
DEPLOY = (ROOT / "scripts" / "deploy_all.sh").read_text(encoding="utf-8")
SHA = "a" * 40
CFG = "batch:\n  phase3: false\n"
VERSION = f"{SHA}+cfg-{hashlib.sha256(CFG.encode()).hexdigest()[:8]}"


@pytest.fixture
def fake(tmp_path):
    """A fake aws: `s3 cp` reads/writes s3://bucket/key under tmp/store, logs argv."""
    store = tmp_path / "store"
    store.mkdir()
    log = tmp_path / "aws.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    aws = bin_dir / "aws"
    aws.write_text(
        "#!/bin/bash\n"
        f'echo "$*" >> "{log}"\n'
        'if [ "$1 $2" = "sts get-caller-identity" ]; then echo 123456789012; exit 0; fi\n'
        'if [ "$1 $2" = "s3 cp" ]; then\n'
        '  src="$3"; dst="$4"\n'
        f'  st="{store}"\n'
        '  if [[ "$dst" == s3://* ]]; then p="$st/${dst#s3://}"; mkdir -p "$(dirname "$p")";\n'
        '    if [ "$src" = - ]; then cat > "$p"; else cp "$src" "$p"; fi; exit 0; fi\n'
        '  p="$st/${src#s3://}"; [ -f "$p" ] || exit 1\n'
        '  if [ "$dst" = - ]; then cat "$p"; else cp "$p" "$dst"; fi; exit 0\n'
        "fi\n"
        "exit 0\n"
    )
    aws.chmod(aws.stat().st_mode | stat.S_IEXEC)
    return tmp_path, store, log, bin_dir


def _sh(fake, body: str, cwd=None):
    tmp_path, _, _, bin_dir = fake
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    return subprocess.run(
        ["bash", "-c", f"set -e\nsource '{ARCHIVE}'\n{body}"],
        cwd=cwd or tmp_path,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_put_stores_the_file_under_its_version(fake):
    tmp_path, store, log, _ = fake
    (tmp_path / "config.yaml").write_text(CFG)
    r = _sh(fake, f"config_archive_put dev us-east-1 '{VERSION}' config.yaml")
    assert r.returncode == 0, r.stdout + r.stderr
    obj = store / "dev-wwii-config-123456789012" / "deployed" / VERSION / "config.yaml"
    assert obj.read_text() == CFG
    latest = store / "dev-wwii-config-123456789012" / "deployed" / "LATEST"
    assert latest.read_text().strip() == VERSION
    assert "--sse AES256" in log.read_text()


@pytest.mark.parametrize(
    "version", [SHA, "unknown", f"{SHA}+profile-x", f"{SHA}+cfg-xyz", "../x"]
)
def test_put_refuses_an_unexpected_version(fake, version):
    tmp_path, store, _, _ = fake
    (tmp_path / "config.yaml").write_text(CFG)
    r = _sh(fake, f"config_archive_put dev us-east-1 '{version}' config.yaml")
    assert r.returncode == 1
    assert "unexpected version" in r.stderr
    assert not any(store.rglob("config.yaml"))


def test_put_refuses_an_empty_config(fake):
    tmp_path, _, _, _ = fake
    (tmp_path / "config.yaml").write_text("")
    r = _sh(fake, f"config_archive_put dev us-east-1 '{VERSION}' config.yaml")
    assert r.returncode == 1 and "missing or empty" in r.stderr


def test_get_latest_round_trips_and_verifies_the_hash(fake):
    tmp_path, _, _, _ = fake
    (tmp_path / "config.yaml").write_text(CFG)
    _sh(fake, f"config_archive_put dev us-east-1 '{VERSION}' config.yaml")
    r = _sh(fake, "config_archive_get dev us-east-1 latest restored.yaml")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tmp_path / "restored.yaml").read_text() == CFG
    assert "hash verified" in r.stdout
    assert oct((tmp_path / "restored.yaml").stat().st_mode)[-3:] == "600"


def test_get_rejects_a_file_that_does_not_match_its_version(fake):
    tmp_path, store, _, _ = fake
    (tmp_path / "config.yaml").write_text(CFG)
    _sh(fake, f"config_archive_put dev us-east-1 '{VERSION}' config.yaml")
    obj = store / "dev-wwii-config-123456789012" / "deployed" / VERSION / "config.yaml"
    obj.write_text("tampered: true\n")
    r = _sh(fake, f"config_archive_get dev us-east-1 '{VERSION}' restored.yaml")
    assert r.returncode == 1 and "does not match its version" in r.stderr


@pytest.mark.parametrize("dest", ["config.yaml", "sub/config.yaml"])
def test_get_never_overwrites_config_yaml(fake, dest):
    r = _sh(fake, f"config_archive_get dev us-east-1 '{VERSION}' {dest}")
    assert r.returncode == 1 and "will not overwrite config.yaml" in r.stderr


def test_get_rejects_a_non_version(fake):
    r = _sh(fake, "config_archive_get dev us-east-1 '../../etc' out.yaml")
    assert r.returncode == 1 and "not a deploy version" in r.stderr


def test_the_deploy_archives_after_the_stacks_and_stops_on_failure():
    step5 = DEPLOY.index("=== 5. Deploying CloudFormation ===")
    step5b = DEPLOY.index("=== 5b. Archiving the deployed config.yaml ===")
    step6 = DEPLOY.index("=== 6. Updating Lambda code ===")
    assert step5 < step5b < step6
    block = DEPLOY[step5b:step6]
    assert "source scripts/config_archive.sh" in block
    assert 'config_archive_put "$ENV" "$REGION" "$GIT_SHA" config.yaml' in block
    assert "exit 1" in block


class _CfnLoader(yaml.SafeLoader):
    pass


def _tag(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


_CfnLoader.add_multi_constructor("!", _tag)


def _storage():
    text = (ROOT / "cloudformation" / "storage.yaml").read_text(encoding="utf-8")
    return yaml.load(text, Loader=_CfnLoader)[
        "Resources"
    ]  # noqa: S506 - SafeLoader subclass


def test_the_config_bucket_is_private_encrypted_and_versioned():
    b = _storage()["ConfigBucket"]
    assert b["DeletionPolicy"] == "Retain"
    p = b["Properties"]
    assert all(p["PublicAccessBlockConfiguration"].values())
    assert len(p["PublicAccessBlockConfiguration"]) == 4
    enc = p["BucketEncryption"]["ServerSideEncryptionConfiguration"][0]
    assert enc["ServerSideEncryptionByDefault"]["SSEAlgorithm"] == "AES256"
    assert p["VersioningConfiguration"]["Status"] == "Enabled"
    assert (
        p["OwnershipControls"]["Rules"][0]["ObjectOwnership"] == "BucketOwnerEnforced"
    )


def test_the_config_bucket_denies_insecure_transport():
    stmts = _storage()["ConfigBucketPolicy"]["Properties"]["PolicyDocument"][
        "Statement"
    ]
    deny = next(s for s in stmts if s["Sid"] == "DenyInsecureTransport")
    assert deny["Effect"] == "Deny" and deny["Principal"] == "*"
    assert deny["Condition"]["Bool"]["aws:SecureTransport"] == "false"


def test_no_pipeline_role_is_granted_the_config_bucket():
    for name in ("iam.yaml", "compute.yaml"):
        text = (ROOT / "cloudformation" / name).read_text(encoding="utf-8")
        assert "wwii-config" not in text and "ConfigBucket" not in text, name


def test_a_fetched_config_is_gitignored():
    assert "config.yaml.restored" in (ROOT / ".gitignore").read_text().splitlines()


def test_put_refuses_a_file_that_changed_since_the_version_was_stamped(fake):
    tmp_path, store, _, _ = fake
    (tmp_path / "config.yaml").write_text(
        "batch:\n  phase3: true\n"
    )  # not what VERSION names
    r = _sh(fake, f"config_archive_put dev us-east-1 '{VERSION}' config.yaml")
    assert r.returncode == 1 and "changed since the version was stamped" in r.stderr
    assert not any(store.rglob("config.yaml"))


def test_the_list_query_handles_an_empty_bucket():
    import jmespath

    text = ARCHIVE.read_text(encoding="utf-8")
    query = text.split('--query "', 1)[1].split('"', 1)[0].replace("\\`", "`")
    assert jmespath.search(query, {}) == []
    listed = jmespath.search(
        query,
        {
            "Contents": [
                {"Key": "deployed/b/config.yaml", "LastModified": "2"},
                {"Key": "deployed/LATEST", "LastModified": "3"},
                {"Key": "deployed/a/config.yaml", "LastModified": "1"},
            ]
        },
    )
    assert listed == [["1", "deployed/a/config.yaml"], ["2", "deployed/b/config.yaml"]]


def test_sourcing_does_not_change_the_callers_shell_options(fake):
    r = _sh(fake, "set +o | grep -E 'nounset|pipefail'")
    assert "set +o nounset" in r.stdout and "set +o pipefail" in r.stdout


def test_the_script_and_the_template_name_the_same_bucket():
    tpl = (ROOT / "cloudformation" / "storage.yaml").read_text(encoding="utf-8")
    assert "BucketName: !Sub ${EnvironmentName}-wwii-config-${AWS::AccountId}" in tpl
    assert "printf '%s' \"${env}-wwii-config-${account}\"" in ARCHIVE.read_text()


def test_pipeline_roles_are_denied_the_config_bucket():
    stmts = _storage()["ConfigBucketPolicy"]["Properties"]["PolicyDocument"][
        "Statement"
    ]
    deny = next(s for s in stmts if s["Sid"] == "DenyPipelineRoles")
    assert deny["Effect"] == "Deny" and deny["Action"] == "s3:*"
    assert deny["Condition"]["ArnLike"]["aws:PrincipalArn"].endswith(
        ":role/${EnvironmentName}-wwii-*"
    )
