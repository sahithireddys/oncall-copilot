"""Small, dependency-free BM25 (Okapi) index.

BM25 is a strong baseline for incident text: error strings, service names and
exception class names are exact tokens, which lexical search matches better
than general-purpose embeddings. The `Retriever` protocol in store.py lets you
swap in an embedding index later without touching the agent.
"""
from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN = re.compile(r"[a-z0-9_]+(?:[.-][a-z0-9_]+)*")
STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are", "was", "were", "be", "by", "with",
    "at", "from", "it", "this", "that", "as", "if", "then", "not", "no", "use", "when", "into", "its", "their",
}


def tokenize(text: str) -> list[str]:
    toks = []
    for tok in _TOKEN.findall(text.lower()):
        if tok in STOPWORDS or len(tok) < 2:
            continue
        toks.append(tok)
        # also index the parts of compound tokens: "valkey-cart" -> valkey, cart
        if "-" in tok or "." in tok:
            toks.extend(p for p in re.split(r"[.-]", tok) if len(p) > 1 and p not in STOPWORDS)
    return toks


class BM25Index:
    def __init__(self, k1: float = 1.4, b: float = 0.75):
        self.k1, self.b = k1, b
        self.doc_ids: list[str] = []
        self.doc_tf: list[Counter] = []
        self.doc_len: list[int] = []
        self.df: Counter = Counter()

    def add(self, doc_id: str, text: str) -> None:
        toks = tokenize(text)
        tf = Counter(toks)
        self.doc_ids.append(doc_id)
        self.doc_tf.append(tf)
        self.doc_len.append(len(toks))
        self.df.update(tf.keys())

    def __len__(self) -> int:
        return len(self.doc_ids)

    def search(self, query: str, k: int = 5) -> list[tuple[str, float]]:
        if not self.doc_ids:
            return []
        q = Counter(tokenize(query))
        n = len(self.doc_ids)
        avgdl = sum(self.doc_len) / n or 1.0
        scores = []
        for i, tf in enumerate(self.doc_tf):
            s = 0.0
            for term, qf in q.items():
                f = tf.get(term)
                if not f:
                    continue
                idf = math.log(1 + (n - self.df[term] + 0.5) / (self.df[term] + 0.5))
                denom = f + self.k1 * (1 - self.b + self.b * self.doc_len[i] / avgdl)
                s += idf * f * (self.k1 + 1) / denom * (1 + 0.2 * (qf - 1))
            if s > 0:
                scores.append((self.doc_ids[i], s))
        scores.sort(key=lambda x: -x[1])
        return scores[:k]
