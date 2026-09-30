"""Post the hypothesis to Slack as a Block Kit message (incoming webhook)."""
from __future__ import annotations

import logging

import httpx

from ..models import Investigation

log = logging.getLogger(__name__)

EMOJI = {"critical": ":rotating_light:", "warning": ":warning:", "info": ":information_source:"}


def build_blocks(inv: Investigation, ui_url: str) -> dict:
    h = inv.hypothesis
    a = inv.alert
    link = f"{ui_url.rstrip('/')}/investigations/{inv.id}"
    if h is None:
        return {"text": f"Investigation {inv.id} for {a.name} failed: {inv.error}"}
    ev_by_id = {e.id: e for e in inv.evidence}
    evidence_lines = []
    for ref in h.supporting_evidence[:5]:
        ev = ev_by_id.get(ref.evidence_id)
        label = ev.title if ev else ref.evidence_id
        if ev and ev.link:
            label = f"<{ev.link}|{label}>"
        evidence_lines.append(f"• {label} — _{ref.why_it_matters}_")
    actions = [f"{i + 1}. *{x.title}* ({x.risk} risk)" + (f"\n   `{x.command}`" if x.command else "")
               for i, x in enumerate(h.suggested_actions[:3])]
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"{a.name} · {a.service}"[:150]}},
        {"type": "section", "text": {"type": "mrkdwn", "text":
            f"{EMOJI.get(a.severity, '')} *Likely root cause:* `{h.root_cause_service}` — "
            f"{h.category.value.replace('_', ' ')} ({h.confidence:.0%} confidence)\n*{h.title}*\n{h.summary}"}},
    ]
    if evidence_lines:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*Evidence*\n" + "\n".join(evidence_lines)}})
    if actions:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*Suggested next steps*\n" + "\n".join(actions)}})
    if h.related_documents:
        blocks.append({"type": "context", "elements": [
            {"type": "mrkdwn", "text": "Similar: " + ", ".join(h.related_documents)}]})
    blocks.append({"type": "actions", "elements": [
        {"type": "button", "text": {"type": "plain_text", "text": "Open investigation"}, "url": link, "style": "primary"},
        {"type": "button", "text": {"type": "plain_text", "text": ":white_check_mark: Correct"}, "url": f"{link}?feedback=correct"},
        {"type": "button", "text": {"type": "plain_text", "text": ":x: Wrong"}, "url": f"{link}?feedback=incorrect"},
    ]})
    return {"text": f"{a.name}: likely {h.root_cause_service} ({h.category.value})", "blocks": blocks}


def post_to_slack(inv: Investigation, webhook_url: str, ui_url: str) -> bool:
    if not webhook_url:
        return False
    try:
        r = httpx.post(webhook_url, json=build_blocks(inv, ui_url), timeout=10)
        r.raise_for_status()
        return True
    except Exception as exc:
        log.warning("slack post failed: %s", exc)
        return False
