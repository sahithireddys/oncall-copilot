from copilot.models import FeedbackIn, FeedbackVerdict, InvestigationStatus, RootCauseCategory

from .helpers import make_service


def test_scenario_investigation_is_graded_and_persisted(tmp_path):
    s = make_service(tmp_path)
    inv = s.investigate_scenario("cart-readiness-probe", background=False)
    assert inv.status == InvestigationStatus.completed
    assert inv.grading["correct"] is True
    again = s.get_investigation(inv.id)
    assert again.hypothesis.root_cause_service == "cart"
    assert len(s.store.list_events(inv.id)) > 5


def test_feedback_teaches_the_knowledge_base(tmp_path):
    s = make_service(tmp_path)
    inv = s.investigate_scenario("ad-high-cpu", background=False)
    before = len(s.kb.docs)
    s.submit_feedback(inv.id, FeedbackIn(verdict=FeedbackVerdict.correct, notes="regex hot loop again"))
    assert len(s.kb.docs) == before + 1
    hits = s.kb.search("ad cpu saturation regex hot loop", k=3)
    assert any(d.id == f"INC-{inv.id}" for d, _ in hits)
    # a fresh service instance reloads learned incidents from the database
    s2 = make_service(tmp_path)
    assert f"INC-{inv.id}" in s2.kb.docs


def test_wrong_verdict_with_correction_is_learned_with_the_corrected_cause(tmp_path):
    s = make_service(tmp_path)
    inv = s.investigate_scenario("valkey-slow", background=False)
    s.submit_feedback(inv.id, FeedbackIn(verdict=FeedbackVerdict.incorrect, actual_root_cause_service="valkey-cart",
                                         actual_category=RootCauseCategory.datastore_degradation))
    doc = s.kb.get(f"INC-{inv.id}")
    assert doc.category == "datastore_degradation"


def test_stats(tmp_path):
    s = make_service(tmp_path)
    inv = s.investigate_scenario("payment-bad-deploy", background=False)
    s.submit_feedback(inv.id, FeedbackIn(verdict=FeedbackVerdict.correct))
    st = s.stats()
    assert st["completed"] == 1 and st["feedback"]["accuracy"] == 1.0 and st["scenario_accuracy"] == 1.0


def test_eval_runner(tmp_path):
    from copilot.evals.runner import run_eval
    s = make_service(tmp_path)
    run = run_eval(s, reasoner="heuristic", scenario_ids=["payment-bad-deploy", "valkey-slow", "kafka-queue-problems"])
    assert run["status"] == "completed"
    assert run["summary"]["n"] == 3
    assert s.store.get_eval_run(run["id"])["summary"]["completed"] == 3
