"""Kubernetes API: warning events, pod health, and change history (rollouts, env/limit changes, scaling)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..models import Evidence
from .base import Window

log = logging.getLogger(__name__)
SERVICE_LABELS = ("app.kubernetes.io/component", "app.kubernetes.io/name", "app")
INTERESTING_REASONS = {"BackOff", "Unhealthy", "OOMKilling", "Killing", "FailedScheduling", "Evicted",
                       "ScalingReplicaSet", "FailedCreate", "CPUThrottlingHigh", "Failed", "FailedMount"}


def _svc_from_labels(labels: dict | None, fallback: str) -> str:
    labels = labels or {}
    for k in SERVICE_LABELS:
        if labels.get(k):
            return labels[k]
    return fallback


def _svc_from_pod_name(name: str) -> str:
    # checkout-5d8f7c9b6d-x2x9q -> checkout
    parts = name.split("-")
    return "-".join(parts[:-2]) if len(parts) > 2 else name


def _ts(dt) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _container_fingerprint(template) -> dict:
    """Image, env and resources of the first container, to diff ReplicaSet revisions."""
    c = template.spec.containers[0]
    env = {e.name: (e.value if e.value is not None else "<ref>") for e in (c.env or [])}
    res = c.resources.to_dict() if c.resources else {}
    return {"image": c.image, "env": env, "limits": (res or {}).get("limits") or {}, "command": c.command}


class KubernetesCluster:
    def __init__(self, namespace: str):
        from kubernetes import client, config  # optional dependency, only needed in live mode

        try:
            config.load_incluster_config()
        except Exception:
            config.load_kube_config()
        self.ns = namespace
        self.core = client.CoreV1Api()
        self.apps = client.AppsV1Api()

    # --------------------------------------------------------------- events
    def get_k8s_events(self, window: Window) -> list[Evidence]:
        out: list[Evidence] = []
        for i, ev in enumerate(self.core.list_namespaced_event(self.ns).items):
            last = _ts(ev.last_timestamp or ev.event_time or ev.metadata.creation_timestamp)
            if not last or last < window.start or ev.reason not in INTERESTING_REASONS:
                continue
            if ev.type != "Warning" and ev.reason not in ("ScalingReplicaSet", "Killing"):
                continue
            obj = f"{ev.involved_object.kind.lower()}/{ev.involved_object.name}"
            svc = _svc_from_pod_name(ev.involved_object.name) if ev.involved_object.kind == "Pod" else ev.involved_object.name
            first = _ts(ev.first_timestamp) or last
            out.append(Evidence(
                id=f"k8s:{svc}:{ev.reason}:{i}", source="k8s_events", service=svc,
                title=f"{ev.reason} ×{ev.count or 1} on {obj}", detail=ev.message or "",
                severity="warning" if ev.type == "Warning" else "info",
                data={"kind": "k8s_event", "reason": ev.reason, "object": obj, "count": ev.count or 1,
                      "message": ev.message or "", "first_seen_min": window.rel_minutes(first)}))
        for pod in self.core.list_namespaced_pod(self.ns).items:
            svc = _svc_from_labels(pod.metadata.labels, _svc_from_pod_name(pod.metadata.name))
            for cs in pod.status.container_statuses or []:
                restarts = cs.restart_count or 0
                waiting = cs.state.waiting.reason if cs.state and cs.state.waiting else None
                last_term = cs.last_state.terminated if cs.last_state else None
                if not restarts and not waiting:
                    continue
                reason = None
                if last_term:
                    reason = last_term.reason if last_term.reason != "Error" else f"Error (exit code {last_term.exit_code})"
                    if _ts(last_term.finished_at) and _ts(last_term.finished_at) < window.start and not waiting:
                        continue  # old restart, outside the window
                out.append(Evidence(
                    id=f"k8s:{svc}:pod_status:{pod.metadata.name}", source="k8s_events", service=svc,
                    title=f"pod/{pod.metadata.name} restarts={restarts}"
                          + (f", last terminated: {reason}" if reason else "") + (f", waiting: {waiting}" if waiting else ""),
                    severity="critical" if restarts >= 3 or waiting else "warning",
                    data={"kind": "pod_status", "pod": f"pod/{pod.metadata.name}", "phase": pod.status.phase,
                          "restarts": restarts, "last_termination_reason": reason, "waiting_reason": waiting}))
        return out

    # -------------------------------------------------------------- changes
    def get_changes(self, window: Window) -> list[Evidence]:
        out: list[Evidence] = []
        by_deploy: dict[str, list] = {}
        for rs in self.apps.list_namespaced_replica_set(self.ns).items:
            owner = next((o.name for o in (rs.metadata.owner_references or []) if o.kind == "Deployment"), None)
            if owner:
                by_deploy.setdefault(owner, []).append(rs)
        for deploy, rss in by_deploy.items():
            rss.sort(key=lambda r: int((r.metadata.annotations or {}).get("deployment.kubernetes.io/revision", 0)))
            for prev, cur in zip(rss, rss[1:]):
                created = _ts(cur.metadata.creation_timestamp)
                if not created or created < window.start:
                    continue
                svc = _svc_from_labels(cur.spec.template.metadata.labels, deploy)
                a, b = _container_fingerprint(prev.spec.template), _container_fingerprint(cur.spec.template)
                mins = round(-window.rel_minutes(created), 1)
                if a["image"] != b["image"]:
                    out.append(Evidence(
                        id=f"changes:{svc}:deploy:{cur.metadata.name}", source="changes", service=svc,
                        title=f"deployment/{deploy} rolled out {a['image'].split(':')[-1]} → {b['image'].split(':')[-1]} ({mins:.0f}m before alert)",
                        severity="warning" if mins <= 30 else "info",
                        data={"kind": "deploy", "resource": f"deployment/{deploy}", "from_version": a["image"].split(":")[-1],
                              "to_version": b["image"].split(":")[-1], "minutes_before_alert": mins}))
                changed = {k: (a["env"].get(k), b["env"].get(k)) for k in set(a["env"]) | set(b["env"])
                           if a["env"].get(k) != b["env"].get(k)}
                if a["limits"] != b["limits"]:
                    changed["resources.limits"] = (str(a["limits"]), str(b["limits"]))
                if a["command"] != b["command"]:
                    changed["command"] = (str(a["command"]), str(b["command"]))
                for key, (old, new) in changed.items():
                    out.append(Evidence(
                        id=f"changes:{svc}:config:{cur.metadata.name}:{key}", source="changes", service=svc,
                        title=f"deployment/{deploy}: {key} changed {old!r} → {new!r} ({mins:.0f}m before alert)",
                        severity="warning" if mins <= 30 else "info",
                        data={"kind": "config_change", "resource": f"deployment/{deploy}", "key": key, "old": old,
                              "new": new, "minutes_before_alert": mins, "affects": svc}))
        for cm in self.core.list_namespaced_config_map(self.ns).items:
            times = [_ts(m.time) for m in (cm.metadata.managed_fields or []) if m.time]
            last = max(times) if times else None
            if last and last >= window.start and cm.metadata.name not in ("kube-root-ca.crt",):
                mins = round(-window.rel_minutes(last), 1)
                out.append(Evidence(
                    id=f"changes:{cm.metadata.name}:configmap", source="changes", service=cm.metadata.name,
                    title=f"configmap/{cm.metadata.name} modified ({mins:.0f}m before alert)",
                    severity="warning" if mins <= 30 else "info",
                    data={"kind": "config_change", "resource": f"configmap/{cm.metadata.name}", "key": "*",
                          "old": None, "new": None, "minutes_before_alert": mins,
                          "affects": "flagd" if cm.metadata.name == "flagd-config" else cm.metadata.name}))
        for d in self.apps.list_namespaced_deployment(self.ns).items:
            if d.spec.replicas == 0:
                svc = _svc_from_labels(d.spec.template.metadata.labels, d.metadata.name)
                out.append(Evidence(
                    id=f"changes:{svc}:scale", source="changes", service=svc,
                    title=f"deployment/{d.metadata.name} has 0 replicas", severity="warning",
                    data={"kind": "scale", "resource": f"deployment/{d.metadata.name}", "from_replicas": None,
                          "to_replicas": 0, "minutes_before_alert": None}))
        return out
