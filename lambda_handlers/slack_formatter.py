"""Slack notification formatter (UNATTENDED_READINESS #3).

The pipeline publishes FREE-FORM text notifications (task launched, phase complete,
dedup gate, spend/credit alerts) to the notification SNS topic. AWS Chatbot (Amazon
Q Developer in chat applications) SILENTLY DROPS non-alarm free-form text — only
CloudWatch alarms and messages in Chatbot's custom-notification schema render in
Slack (verified 2026-09-28: alarm->Slack worked, free-form pipeline text did not).

This Lambda subscribes to the pipeline notification topic, wraps each message in
Chatbot's custom-notification schema, and republishes to the Slack topic Chatbot
watches:

    pipeline -> {notify topic} -> THIS Lambda -> {slack topic} -> Chatbot -> Slack

Every message is ACTIONABLE (operator requirement): at minimum a "View logs"
CloudWatch deep link in nextSteps; financial notifications (spend/credit/budget)
embed the dollar figures in the description AND add a billing/Cost Explorer link.

Slack replicates the SAME detail email receives: the Slack formatter and the
email endpoint both subscribe to the one `phase2-complete` SNS topic, so they get
the identical message. The full body is carried in `description` (Chatbot allows
8,000 chars of Slack markdown) — structured here (JSON bodies rendered as readable
key/value lines; a distinct Subject surfaced as a bold lead line) so the channel
shows the same content email does rather than a one-line summary.

Schema: https://docs.aws.amazon.com/chatbot/latest/adminguide/custom-notifs.html
    {"version": "1.0", "source": "custom", "content": {"description", "nextSteps", ...}}

Self-contained; no heavy imports.
"""

import json
import logging
import os
import re

import boto3

logger = logging.getLogger()
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

REGION = os.getenv("AWS_REGION", "us-east-1")
ENV_NAME = os.getenv("ENV_NAME", "dev")
SLACK_TOPIC_ARN = os.environ.get("SLACK_TOPIC_ARN", "")

# Map subject/message keywords to a leading emoji for quick visual triage.
# Order matters: the FIRST matching rule wins. Review/gate is checked BEFORE
# failure so a dedup "needs-review"/"gate hold" reads as an action item (:warning:)
# rather than an error (:x:) — the owner-flagged false-alarm (2026-10-01) where a
# dedup gate "HOLD" rendered like a pipeline failure. "hold"/"gate"/"review"/
# "duplicate" are review signals, NOT failures; genuine failures use fail/error/
# denied/not-authorized/exception.
_EMOJI_RULES = (
    (("spend", "credit", "budget", "cost", "$"), ":moneybag:"),
    (
        ("review", "gate", "needs-review", "duplicate", "hold", "queued", "awaiting"),
        ":warning:",
    ),
    (("fail", "error", "not authorized", "denied", "exception", "aborted"), ":x:"),
    (("warn",), ":warning:"),
    (("complete", "success", "done", "proceed"), ":white_check_mark:"),
    (("launched", "started", "starting"), ":rocket:"),
)

_FINANCIAL_KEYWORDS = ("spend", "credit", "budget", "cost", "$", "usd", "dollar")
# Dollar amounts like $12.34 / $1,234 / 12.5 USD
_MONEY_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?(?:USD|usd)")


def _sns():
    return boto3.client("sns", region_name=REGION)


def _emoji_for(text: str) -> str:
    low = text.lower()
    for keywords, emoji in _EMOJI_RULES:
        if any(k in low for k in keywords):
            return emoji
    return ":information_source:"


def _cw_console(path: str) -> str:
    return f"https://{REGION}.console.aws.amazon.com/cloudwatch/home?region={REGION}#{path}"


def _logs_link(text: str) -> str:
    """A CloudWatch Logs deep link appropriate to the notification's phase/source.
    Every message gets at least this so an operator can always jump to logs."""
    low = text.lower()
    ecs_group = f"/ecs/{ENV_NAME}-wwii-pipeline"
    group = ecs_group
    if any(k in low for k in ("ocr", "chandra")):
        group = "/aws/batch/job"
    elif any(k in low for k in ("trigger", "queued", "launched")):
        group = f"/aws/lambda/{ENV_NAME}-wwii-trigger"
    # logsV2 log-group deep link (group name is URL-encoded with $252F for '/').
    enc = group.replace("/", "$252F")
    url = _cw_console(f"logsV2:log-groups/log-group/{enc}")
    return f"<{url}|View logs ({group})>"


def _is_financial(text: str) -> bool:
    low = text.lower()
    return any(k in low for k in _FINANCIAL_KEYWORDS)


def _next_steps(subject: str, message: str) -> list:
    """Actionable steps. ALWAYS includes a View-logs link; financial messages also
    get a billing/Cost Explorer link."""
    text = f"{subject}\n{message}"
    steps = [_logs_link(text)]
    if _is_financial(text):
        ce_url = "https://console.aws.amazon.com/cost-management/home#/cost-explorer"
        billing_url = "https://console.aws.amazon.com/billing/home#/bills"
        steps.append(f"<{ce_url}|View Cost Explorer>")
        steps.append(f"<{billing_url}|View current bill>")
    return steps


def _coerce_body(message: str) -> str:
    """Normalize the raw SNS message into human-readable text.

    Most pipeline notifications are already free-form multi-line text and pass
    through unchanged (Chatbot renders ``description`` in Slack markdown, so the
    newlines/`*bold*` survive). A few publishers send a JSON blob (e.g. the dedup
    UI's ``{"dedup_complete": true}``); render those as readable ``key: value``
    lines instead of dumping raw JSON into the channel.
    """
    body = (message or "").strip()
    if not body:
        return ""
    if body[0] in "{[":
        try:
            parsed = json.loads(body)
        except (ValueError, TypeError):
            return body
        return _render_parsed_body(parsed, body)
    return body


def _render_parsed_body(parsed, body: str) -> str:
    """Render a parsed JSON dict/list as readable ``key: value`` / bullet lines (falling back to
    the raw ``body`` when empty or not a dict/list). Preserves the formatting exactly.
    """
    if isinstance(parsed, dict):
        return "\n".join(f"*{k}:* {v}" for k, v in parsed.items()) or body
    if isinstance(parsed, list):
        return "\n".join(f"• {item}" for item in parsed) or body
    return body


def _description(subject: str, message: str) -> str:
    """Full message body, structured for Slack — carries the SAME detail email
    gets (both subscribe to the same SNS topic). We do NOT drop content: the whole
    body is preserved (Chatbot allows 8,000 chars of Slack markdown).

    - JSON bodies are rendered as readable key/value lines (not raw JSON).
    - The Subject is surfaced as a bold lead line when it carries context not
      already present at the start of the body (so operators see the same framing
      email shows in its subject line, which Slack otherwise buries in the title).
    - Financial notifications still get the detected dollar amounts pinned on top.
    """
    body = _coerce_body(message)
    subj = (subject or "").strip()

    # Surface the subject as a bold lead when it's distinct from the body lead
    # (avoid duplicating when the body already starts with the subject text).
    if subj and not body.lower().startswith(subj.lower()[:40]):
        body = f"*{subj}*\n{body}" if body else f"*{subj}*"

    if _is_financial(f"{subject} {message}"):
        amounts = _MONEY_RE.findall(f"{subject} {message}")
        if amounts:
            uniq = list(dict.fromkeys(a.strip() for a in amounts))
            body = f"*Amount(s): {', '.join(uniq)}*\n{body}"
    return body[:8000]


def _to_chatbot_custom(subject: str, message: str) -> dict:
    """Wrap a free-form pipeline notification in Chatbot's custom-notification
    schema so it renders in Slack — with actionable nextSteps + embedded finances."""
    title = (subject or "WWII Pipeline").strip()
    emoji = _emoji_for(f"{subject} {message}")
    financial = _is_financial(f"{subject} {message}")
    content: dict = {
        "textType": "client-markdown",
        "title": f"{emoji} {title}"[:250],
        "description": _description(subject, message),
        "nextSteps": [s[:350] for s in _next_steps(subject, message)],
    }
    if financial:
        content["keywords"] = ["cost", ENV_NAME]
    return {"version": "1.0", "source": "custom", "content": content}


def handler(event, _context):
    """SNS -> reformat each record -> republish to the Slack (Chatbot) topic."""
    if not SLACK_TOPIC_ARN:
        logger.error("SLACK_TOPIC_ARN not set — cannot forward to Slack")
        return {"action": "error", "reason": "no slack topic"}

    sns = _sns()
    forwarded = 0
    for record in event.get("Records", []):
        sns_rec = record.get("Sns", {})
        subject = sns_rec.get("Subject") or ""
        message = sns_rec.get("Message") or ""
        # Already in Chatbot custom schema? (re-delivery) — skip to avoid loops.
        try:
            parsed = json.loads(message)
            if isinstance(parsed, dict) and parsed.get("source") == "custom":
                logger.info("Message already custom-formatted; skipping")
                continue
        except (ValueError, TypeError):
            pass  # free-form text — the expected case
        payload = _to_chatbot_custom(subject, message)
        sns.publish(TopicArn=SLACK_TOPIC_ARN, Message=json.dumps(payload))
        forwarded += 1
        logger.info("Forwarded to Slack: %s", payload["content"]["title"])
    return {"action": "forwarded", "count": forwarded}
