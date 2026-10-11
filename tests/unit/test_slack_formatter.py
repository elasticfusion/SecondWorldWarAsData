"""Tests for the Slack notification formatter Lambda (UNATTENDED_READINESS #3)."""

import json
import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ["SLACK_TOPIC_ARN"] = "arn:aws:sns:us-east-1:111122223333:test-slack"
os.environ["ENV_NAME"] = "dev"

from lambda_handlers import slack_formatter as sf


def _sns_event(
    subject="WWII Pipeline: extract started",
    message="Pipeline task launched: extract",
):
    return {"Records": [{"Sns": {"Subject": subject, "Message": message}}]}


def test_schema_required_fields():
    out = sf._to_chatbot_custom("Subj", "Body")
    assert out["version"] == "1.0"
    assert out["source"] == "custom"
    # Subject is distinct from the body lead -> surfaced as a bold lead line so
    # Slack shows the same framing email carries in its subject.
    assert out["content"]["description"] == "*Subj*\nBody"
    assert out["content"]["textType"] == "client-markdown"


def test_full_multiline_body_detail_preserved():
    """Slack must replicate the detailed email body — the whole multi-line body
    survives (not just a one-line summary)."""
    msg = (
        "Phase 2 failed with exit code 1.\n"
        "Bucket: dev-wwii-data-pipeline\n\n"
        "Check logs: aws logs tail /ecs/dev-wwii-pipeline --since 30m"
    )
    out = sf._to_chatbot_custom("WWII Pipeline: Phase 2 FAILED", msg)
    desc = out["content"]["description"]
    # every line of the body is present
    assert "exit code 1" in desc
    assert "Bucket: dev-wwii-data-pipeline" in desc
    assert "aws logs tail /ecs/dev-wwii-pipeline" in desc


def test_subject_surfaced_when_distinct_from_body():
    out = sf._to_chatbot_custom("WWII Pipeline: dedup gate", "Reason: 3 duplicates")
    assert out["content"]["description"].startswith("*WWII Pipeline: dedup gate*\n")
    assert "Reason: 3 duplicates" in out["content"]["description"]


def test_subject_not_duplicated_when_body_leads_with_it():
    """If the body already starts with the subject text, don't prepend it twice."""
    out = sf._to_chatbot_custom("Enrichment started", "Enrichment started\nBook: B460")
    desc = out["content"]["description"]
    assert desc.count("Enrichment started") == 1


def test_json_body_rendered_readable_not_raw():
    """A JSON blob body (e.g. dedup UI) renders as readable key/value lines."""
    out = sf._to_chatbot_custom("Dedup", json.dumps({"dedup_complete": True}))
    desc = out["content"]["description"]
    assert "dedup_complete" in desc
    assert "{" not in desc  # not raw JSON


def test_empty_body_falls_back_to_subject():
    out = sf._to_chatbot_custom("WWII Pipeline: task launched", "")
    # subject is the only content -> surfaced as the bold description
    assert out["content"]["description"] == "*WWII Pipeline: task launched*"


def test_every_message_has_view_logs_action():
    """Operator requirement: EVERY message is actionable — at minimum View logs."""
    out = sf._to_chatbot_custom("Phase 1 complete", "parse done for B460")
    steps = out["content"]["nextSteps"]
    assert any("View logs" in s for s in steps)
    assert any("console.aws.amazon.com/cloudwatch" in s for s in steps)


def test_logs_link_targets_right_group_per_source():
    assert "batch$252Fjob" in sf._logs_link("OCR chandra-B460 succeeded")
    assert "dev-wwii-trigger" in sf._logs_link("Pipeline task launched")
    assert "ecs$252Fdev-wwii-pipeline" in sf._logs_link("Phase 2 extract complete")


def test_financial_message_embeds_amounts_and_billing_links():
    """Operator requirement: financial outputs embedded in the message body."""
    out = sf._to_chatbot_custom(
        "WWII Pipeline: spend alert",
        "Soft spend alert: $12.50 of $30.00 budget used (credit gating on)",
    )
    desc = out["content"]["description"]
    # amounts embedded prominently
    assert "$12.50" in desc and "$30.00" in desc
    assert desc.startswith("*Amount(s):")
    steps = out["content"]["nextSteps"]
    assert any("Cost Explorer" in s for s in steps)
    assert any("current bill" in s for s in steps)
    assert out["content"]["title"].startswith(":moneybag:")


def test_non_financial_has_no_billing_links():
    out = sf._to_chatbot_custom("Phase 1 complete", "parse done")
    steps = out["content"]["nextSteps"]
    assert not any("Cost Explorer" in s for s in steps)
    assert len(steps) == 1  # just View logs


def test_emoji_success_vs_failure_vs_launch_vs_money():
    assert sf._emoji_for("Phase 1 complete") == ":white_check_mark:"
    assert sf._emoji_for("extraction failed") == ":x:"
    assert sf._emoji_for("Pipeline task launched: extract") == ":rocket:"
    assert sf._emoji_for("spend alert $10") == ":moneybag:"
    assert sf._emoji_for("content queued for review") == ":warning:"
    assert sf._emoji_for("some neutral status") == ":information_source:"


def test_description_truncated_to_8000():
    out = sf._to_chatbot_custom("t", "x" * 9000)
    assert len(out["content"]["description"]) == 8000


def test_nextsteps_each_capped_350():
    out = sf._to_chatbot_custom("spend $5", "y" * 500)
    assert all(len(s) <= 350 for s in out["content"]["nextSteps"])


def test_handler_forwards_freeform_to_slack_topic():
    sns = MagicMock()
    with patch.object(sf, "_sns", return_value=sns):
        out = sf.handler(_sns_event(), None)
    assert out == {"action": "forwarded", "count": 1}
    pub = sns.publish.call_args.kwargs
    assert pub["TopicArn"] == sf.SLACK_TOPIC_ARN
    body = json.loads(pub["Message"])
    assert body["source"] == "custom"
    assert body["content"]["title"].startswith(":rocket:")
    assert any("View logs" in s for s in body["content"]["nextSteps"])


def test_handler_skips_already_custom_formatted():
    """Message already in Chatbot custom schema must NOT be re-forwarded (loop guard)."""
    already = json.dumps(
        {"version": "1.0", "source": "custom", "content": {"description": "x"}}
    )
    sns = MagicMock()
    with patch.object(sf, "_sns", return_value=sns):
        out = sf.handler(
            {"Records": [{"Sns": {"Subject": "", "Message": already}}]}, None
        )
    assert out["count"] == 0
    sns.publish.assert_not_called()


def test_handler_errors_without_slack_topic():
    with patch.object(sf, "SLACK_TOPIC_ARN", ""):
        out = sf.handler(_sns_event(), None)
    assert out["action"] == "error"


def test_handler_multiple_records():
    sns = MagicMock()
    ev = {
        "Records": [
            {"Sns": {"Subject": "A started", "Message": "task launched: A"}},
            {"Sns": {"Subject": "B complete", "Message": "phase complete: B"}},
        ]
    }
    with patch.object(sf, "_sns", return_value=sns):
        out = sf.handler(ev, None)
    assert out["count"] == 2
    assert sns.publish.call_count == 2
