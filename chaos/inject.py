"""Fault injection for the OpenTelemetry Demo running on Kubernetes.

    python chaos/inject.py list
    python chaos/inject.py apply payment-failure-flag              # break it
    python chaos/inject.py apply payment-failure-flag --fire 5     # ...and fire the alert at the copilot after 5 min
    python chaos/inject.py revert payment-failure-flag             # fix it

Requires kubectl pointed at the cluster, and (for chaos-mesh scenarios) Chaos Mesh installed.
The copilot must run with TELEMETRY_MODE=live for the investigation to read real telemetry;
the scenario id is attached as a label so the result is graded against ground truth.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "data" / "scenarios" / "catalog.yaml"
NS = "otel-demo"


def load() -> dict[str, dict]:
    return {s["id"]: s for s in yaml.safe_load(CATALOG.read_text())["scenarios"]}


def sh(cmd: str, check: bool = True, capture: bool = False) -> str:
    print(f"$ {cmd}")
    res = subprocess.run(cmd, shell=True, check=check, text=True, capture_output=capture)
    return res.stdout if capture else ""


def set_flag(flag: str, variant: str, ns: str) -> None:
    raw = sh(f"kubectl -n {ns} get configmap flagd-config -o json", capture=True)
    cm = json.loads(raw)
    key = next(k for k in cm["data"] if k.endswith(".json"))
    cfg = json.loads(cm["data"][key])
    if flag not in cfg["flags"]:
        sys.exit(f"flag {flag!r} not found; available: {', '.join(cfg['flags'])}")
    variants = cfg["flags"][flag]["variants"]
    if variant not in variants:
        sys.exit(f"variant {variant!r} not in {list(variants)} for {flag}")
    cfg["flags"][flag]["defaultVariant"] = variant
    patch = json.dumps({"data": {key: json.dumps(cfg, indent=2)}})
    subprocess.run(["kubectl", "-n", ns, "patch", "configmap", "flagd-config", "--type=merge", "-p", patch], check=True)
    print(f"flag {flag} -> {variant} (flagd picks up ConfigMap changes within ~1 minute)")


def apply(sc: dict, ns: str) -> None:
    rep = sc.get("reproduce", {})
    method = rep.get("method", "manual")
    if method == "manual":
        print(f"manual scenario: {rep.get('notes', '')}")
        return
    for cmd in rep.get("commands", []):
        sh(cmd.replace("-n otel-demo", f"-n {ns}"))
    if method == "flagd":
        set_flag(rep["flag"], rep.get("variant", "on"), ns)
    if rep.get("manifest"):
        sh(f"kubectl apply -f {ROOT / rep['manifest']}")


def revert(sc: dict, ns: str) -> None:
    rep = sc.get("reproduce", {})
    if rep.get("method") == "flagd":
        set_flag(rep["flag"], "off", ns)
    if rep.get("manifest"):
        sh(f"kubectl delete -f {ROOT / rep['manifest']} --ignore-not-found", check=False)
    for cmd in rep.get("revert", []):
        sh(cmd.replace("-n otel-demo", f"-n {ns}"), check=False)


def fire(sc: dict, copilot_url: str) -> None:
    a = sc["alert"]
    payload = {"alerts": [{
        "status": "firing",
        "labels": {"alertname": a["name"], "service_name": a["service"], "severity": a.get("severity", "critical"),
                   "scenario_id": sc["id"]},
        "annotations": {"summary": a.get("summary", "")},
        "startsAt": datetime.now(timezone.utc).isoformat(),
    }]}
    r = httpx.post(f"{copilot_url.rstrip('/')}/api/webhooks/alertmanager", json=payload, timeout=10)
    r.raise_for_status()
    print("investigation started:", r.json())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["list", "apply", "revert", "fire"])
    ap.add_argument("scenario", nargs="?")
    ap.add_argument("--namespace", default=NS)
    ap.add_argument("--fire", type=float, metavar="MINUTES", help="after applying, wait and fire the alert")
    ap.add_argument("--copilot", default="http://localhost:8000")
    args = ap.parse_args()
    scenarios = load()
    if args.action == "list":
        for s in scenarios.values():
            print(f"{s['id']:<40} {s.get('reproduce', {}).get('method', 'manual'):<11} {s['title']}")
        return
    if args.scenario not in scenarios:
        sys.exit(f"unknown scenario {args.scenario!r}")
    sc = scenarios[args.scenario]
    if args.action == "apply":
        apply(sc, args.namespace)
        if args.fire is not None:
            print(f"waiting {args.fire} min for symptoms to build up...")
            time.sleep(args.fire * 60)
            fire(sc, args.copilot)
    elif args.action == "revert":
        revert(sc, args.namespace)
    elif args.action == "fire":
        fire(sc, args.copilot)


if __name__ == "__main__":
    main()
