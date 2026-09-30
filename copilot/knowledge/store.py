"""Knowledge base: postmortems + runbooks + incidents confirmed through feedback."""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .bm25 import BM25Index

_FRONT = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.S)


@dataclass
class Document:
    id: str
    title: str
    type: str  # postmortem | runbook | incident
    body: str
    services: list[str] = field(default_factory=list)
    category: str | None = None
    date: str | None = None
    path: str | None = None

    def search_text(self) -> str:
        svc = " ".join(self.services)
        # title and metadata are repeated to weight them above body text
        return f"{self.title} {self.title} {svc} {svc} {self.category or ''} {self.body}"

    def snippet(self, n: int = 400) -> str:
        text = re.sub(r"\s+", " ", self.body).strip()
        return text[:n] + ("…" if len(text) > n else "")

    def as_dict(self, with_body: bool = False) -> dict:
        d = {"id": self.id, "title": self.title, "type": self.type, "services": self.services,
             "category": self.category, "date": self.date, "snippet": self.snippet()}
        if with_body:
            d["body"] = self.body
        return d


def parse_markdown(path: Path) -> Document:
    raw = path.read_text()
    m = _FRONT.match(raw)
    meta, body = ({}, raw) if not m else (yaml.safe_load(m.group(1)) or {}, m.group(2))
    return Document(
        id=str(meta.get("id", path.stem)), title=meta.get("title", path.stem), type=meta.get("type", "runbook"),
        body=body.strip(), services=[str(s) for s in meta.get("services", [])], category=meta.get("category"),
        date=str(meta["date"]) if meta.get("date") else None, path=str(path),
    )


class KnowledgeBase:
    def __init__(self, root: str | Path | None = None):
        self.docs: dict[str, Document] = {}
        self._lock = threading.Lock()
        self._index = BM25Index()
        if root:
            for p in sorted(Path(root).rglob("*.md")):
                doc = parse_markdown(p)
                self.docs[doc.id] = doc
        self._rebuild()

    def _rebuild(self) -> None:
        idx = BM25Index()
        for doc in self.docs.values():
            idx.add(doc.id, doc.search_text())
        self._index = idx

    def add(self, doc: Document) -> None:
        with self._lock:
            self.docs[doc.id] = doc
            self._rebuild()

    def get(self, doc_id: str) -> Document | None:
        return self.docs.get(doc_id)

    def search(self, query: str, k: int = 5, services: list[str] | None = None,
               types: list[str] | None = None) -> list[tuple[Document, float]]:
        hits = self._index.search(query, k=k * 4)
        out = []
        for doc_id, score in hits:
            doc = self.docs[doc_id]
            if types and doc.type not in types:
                continue
            # small boost when the document is about one of the services under suspicion
            if services and any(s in doc.services for s in services):
                score *= 1.25
            out.append((doc, round(score, 3)))
        out.sort(key=lambda x: -x[1])
        return out[:k]

    def list(self) -> list[Document]:
        return sorted(self.docs.values(), key=lambda d: (d.type, d.id))
