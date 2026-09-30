"""Service dependency graph of the OpenTelemetry Demo (v2.x service names).

Used by the simulator to propagate faults upstream, and by the agent as a prior
when traces are sparse. In live mode the graph can be refreshed from Jaeger's
dependency API (see providers/jaeger.py).
"""
from __future__ import annotations

from collections import deque

# caller -> callees
OTEL_DEMO_EDGES: dict[str, list[str]] = {
    "frontend-proxy": ["frontend", "image-provider"],
    "frontend": ["ad", "cart", "checkout", "currency", "product-catalog", "recommendation", "shipping"],
    "checkout": ["cart", "currency", "email", "payment", "product-catalog", "shipping", "kafka"],
    "cart": ["valkey-cart"],
    "recommendation": ["product-catalog"],
    "shipping": ["quote"],
    "kafka": ["accounting", "fraud-detection"],
    "accounting": ["postgresql"],
    "product-catalog": ["postgresql"],
    "ad": [],
    "currency": [],
    "email": [],
    "payment": [],
    "quote": [],
    "fraud-detection": [],
    "valkey-cart": [],
    "postgresql": [],
    "image-provider": [],
}

DATASTORES = {"valkey-cart", "postgresql", "kafka"}

# Share of a caller's requests that touch each callee (used to scale propagated error rates).
CALL_FRACTION: dict[tuple[str, str], float] = {
    ("frontend", "product-catalog"): 0.9,
    ("frontend", "currency"): 0.8,
    ("frontend", "cart"): 0.45,
    ("frontend", "recommendation"): 0.4,
    ("frontend", "ad"): 0.4,
    ("frontend", "checkout"): 0.08,
    ("frontend", "shipping"): 0.15,
    ("frontend-proxy", "frontend"): 0.95,
}


class Topology:
    def __init__(self, edges: dict[str, list[str]] | None = None):
        self.edges = {k: list(v) for k, v in (edges or OTEL_DEMO_EDGES).items()}
        for callees in list(self.edges.values()):
            for c in callees:
                self.edges.setdefault(c, [])

    @property
    def services(self) -> list[str]:
        return sorted(self.edges)

    def callees(self, service: str) -> list[str]:
        return self.edges.get(service, [])

    def callers(self, service: str) -> list[str]:
        return sorted(s for s, cs in self.edges.items() if service in cs)

    def call_fraction(self, caller: str, callee: str) -> float:
        return CALL_FRACTION.get((caller, callee), 1.0)

    def downstream(self, service: str) -> list[str]:
        """All transitive dependencies of `service` (BFS order, nearest first)."""
        seen, order, q = {service}, [], deque([service])
        while q:
            for c in self.callees(q.popleft()):
                if c not in seen:
                    seen.add(c)
                    order.append(c)
                    q.append(c)
        return order

    def upstream(self, service: str) -> list[str]:
        seen, order, q = {service}, [], deque([service])
        while q:
            for c in self.callers(q.popleft()):
                if c not in seen:
                    seen.add(c)
                    order.append(c)
                    q.append(c)
        return order

    def path(self, src: str, dst: str) -> list[str] | None:
        """Shortest call path src -> ... -> dst, or None."""
        prev: dict[str, str | None] = {src: None}
        q = deque([src])
        while q:
            cur = q.popleft()
            if cur == dst:
                out = []
                node: str | None = cur
                while node is not None:
                    out.append(node)
                    node = prev[node]
                return list(reversed(out))
            for c in self.callees(cur):
                if c not in prev:
                    prev[c] = cur
                    q.append(c)
        return None

    def as_dict(self) -> dict:
        return {
            "nodes": [{"id": s, "kind": "datastore" if s in DATASTORES else "service"} for s in self.services],
            "edges": [{"source": s, "target": t} for s, ts in self.edges.items() for t in ts],
        }


DEFAULT_TOPOLOGY = Topology()
