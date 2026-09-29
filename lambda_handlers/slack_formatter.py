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
_EMOJI_RULES = (
    (("fail", "error", "not authorized", "blocked", "hold", "denied"), ":x:"),
    (("spend", "credit", "budget", "cost", "$"), ":moneybag:"),
    (("warn", "queued", "review", "gate"), ":warning:"),
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


def _description(subject: str, message: str) -> str:
    """Message body. For financial notifications, embed the detected dollar
    amounts prominently at the top so the figures are visible without a click."""
    body = (message or subject or "").strip()
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
