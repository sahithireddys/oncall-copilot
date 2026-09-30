from copilot.agent.analysis import extract_findings
from copilot.agent.prompts import SYSTEM_PROMPT, build_user_prompt
from copilot.providers.simulated import SimulatedProvider
from copilot.providers.base import Window

from .helpers import CATALOG, KB


def test_prompt_contains_evidence_ids_and_categories():
    sc = CATALOG["cart-pool-exhaustion"]
    p = SimulatedProvider(sc)
    w = Window(sc.alert.starts_at)
    ev = p.get_metrics("frontend", w) + p.get_traces("frontend", w) + p.get_logs("cart", w) + p.get_changes(w)
    findings = extract_findings(ev, "frontend", p.get_topology())
    docs = [{**d.as_dict(), "score": s} for d, s in KB.search("cart pool", 3)]
    prompt = build_user_prompt(sc.alert, ev, findings, docs, p.get_topology())
    assert "traces:frontend:summary" in prompt
    assert "resource_exhaustion" in prompt
    assert "PM-2026-03-12" in prompt
    assert "red herring" in SYSTEM_PROMPT.lower()
