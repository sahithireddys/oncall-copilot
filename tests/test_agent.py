from copilot.agent.graph import AgentDeps, run_investigation
from copilot.evals.scoring import grade
from copilot.models import EvidenceRef, Hypothesis, RootCauseCategory
from copilot.providers.simulated import SimulatedProvider

from .helpers import CATALOG, KB


def _run(sc, **kw):
    events = []
    deps = AgentDeps(provider=SimulatedProvider(sc), kb=KB, emit=lambda *a: events.append(a), **kw)
    return run_investigation(deps, sc.alert, "test"), events


def test_heuristic_baseline_accuracy_regression_guard():
    """The rule-based baseline should stay >= 85% on the simulated catalog."""
    correct = 0
    for sc in CATALOG.values():
        state, _ = _run(sc)
        correct += grade(state["hypothesis"], sc)["correct"]
    assert correct / len(CATALOG) >= 0.85, f"{correct}/{len(CATALOG)}"


def test_agent_drills_down_from_symptom_to_cause():
    state, events = _run(CATALOG["payment-bad-deploy"])
    assert "payment" in state["examined_services"]  # second round went to the suspect
    assert state["round"] == 2
    nodes = [e[0] for e in events]
    for n in ("triage", "gather", "analyze", "retrieve", "hypothesize", "report"):
        assert n in nodes
    assert any(d["id"] == "PM-2026-01-20" for d in state["documents"])


def test_red_herring_deploy_is_not_blamed():
    state, _ = _run(CATALOG["checkout-memory-leak-payment-noise"])
    assert state["hypothesis"].root_cause_service == "checkout"


class _FailingLLM:
    def hypothesize(self, *a, **k):
        raise TimeoutError("anthropic timed out")


class _HallucinatingLLM:
    def hypothesize(self, alert, evidence, findings, docs, topology, kb=None):
        return Hypothesis(root_cause_service="payment", category=RootCauseCategory.bad_deploy, title="t", summary="s",
                          confidence=0.9, reasoner="fake",
                          supporting_evidence=[EvidenceRef(evidence_id="made-up", why_it_matters="x"),
                                               EvidenceRef(evidence_id="traces:checkout:summary", why_it_matters="y")])


def test_llm_failure_falls_back_to_heuristic():
    state, _ = _run(CATALOG["payment-bad-deploy"], reasoner="claude", llm=_FailingLLM())
    assert state["reasoner_used"] == "heuristic"
    assert state["hypothesis"].root_cause_service == "payment"
    assert any("fallback" in n for n in state["notes"])


def test_report_drops_evidence_the_agent_never_collected():
    state, _ = _run(CATALOG["payment-bad-deploy"], reasoner="claude", llm=_HallucinatingLLM())
    ids = [r.evidence_id for r in state["hypothesis"].supporting_evidence]
    assert ids == ["traces:checkout:summary"]
