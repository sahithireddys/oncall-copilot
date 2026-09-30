from copilot.notify.slack import build_blocks

from .helpers import make_service


def test_slack_blocks(tmp_path):
    s = make_service(tmp_path)
    inv = s.investigate_scenario("payment-bad-deploy", background=False)
    msg = build_blocks(inv, "http://ui")
    text = str(msg["blocks"])
    assert "payment" in text and "rollout undo" in text
    assert msg["blocks"][-1]["type"] == "actions"
