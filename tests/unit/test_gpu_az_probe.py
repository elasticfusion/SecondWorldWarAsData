"""Tests for the deploy-time GPU AZ probe (portable, dynamic 2-AZ derivation)."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
import deploy_aws


def _ec2_with(offerings_by_family):
    """Mock ec2 client whose describe_instance_type_offerings returns per-family AZs."""
    ec2 = MagicMock()

    def _offer(LocationType, Filters):
        fam = Filters[0]["Values"][0]
        return {
            "InstanceTypeOfferings": [
                {"Location": az} for az in offerings_by_family.get(fam, [])
            ]
        }

    ec2.describe_instance_type_offerings.side_effect = _offer
    return ec2


def _session_with(ec2):
    sess = MagicMock()
    sess.client.return_value = ec2
    return sess


def test_probe_picks_first_two_common_gpu_azs():
    ec2 = _ec2_with(
        {
            "g4dn.xlarge": ["us-east-1b", "us-east-1c", "us-east-1d"],
            "g5.xlarge": ["us-east-1b", "us-east-1c", "us-east-1e"],
            "g6.xlarge": ["us-east-1b", "us-east-1c", "us-east-1f"],
        }
    )
    with patch("boto3.Session", return_value=_session_with(ec2)):
        azs = deploy_aws._probe_gpu_azs("us-east-1", None)
    assert azs == ["us-east-1b", "us-east-1c"]  # intersection, first 2


def test_probe_fails_fast_when_no_gpu():
    ec2 = _ec2_with({"g4dn.xlarge": [], "g5.xlarge": [], "g6.xlarge": []})
    with patch("boto3.Session", return_value=_session_with(ec2)):
        with pytest.raises(SystemExit):
            deploy_aws._probe_gpu_azs("ap-nowhere-1", None)


def test_probe_single_az_warns_but_returns_one():
    ec2 = _ec2_with(
        {
            "g4dn.xlarge": ["us-west-2a"],
            "g5.xlarge": ["us-west-2a"],
            "g6.xlarge": ["us-west-2a"],
        }
    )
    with patch("boto3.Session", return_value=_session_with(ec2)):
        azs = deploy_aws._probe_gpu_azs("us-west-2", None)
    assert azs == ["us-west-2a"]  # single-AZ (warned)


def test_probe_falls_back_to_union_when_no_common():
    """No AZ has ALL families -> union so we still find GPU capacity."""
    ec2 = _ec2_with(
        {
            "g4dn.xlarge": ["us-east-1a"],
            "g5.xlarge": ["us-east-1b"],
            "g6.xlarge": ["us-east-1c"],
        }
    )
    with patch("boto3.Session", return_value=_session_with(ec2)):
        azs = deploy_aws._probe_gpu_azs("us-east-1", None)
    assert len(azs) == 2 and set(azs).issubset(
        {"us-east-1a", "us-east-1b", "us-east-1c"}
    )
