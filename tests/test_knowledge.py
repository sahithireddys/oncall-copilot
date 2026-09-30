from copilot.knowledge.bm25 import BM25Index, tokenize
from copilot.knowledge.store import Document, KnowledgeBase

from .helpers import KB


def test_tokenize_splits_compound_service_names():
    toks = tokenize("valkey-cart RedisTimeoutException pool busy=8/8")
    assert "valkey-cart" in toks and "valkey" in toks and "cart" in toks


def test_bm25_ranks_exact_matches_first():
    idx = BM25Index()
    idx.add("a", "payment TypeError after deploy")
    idx.add("b", "cart redis connection pool exhausted")
    idx.add("c", "postgres slow query lock")
    assert idx.search("redis pool timeout in cart")[0][0] == "b"


def test_postmortem_retrieval():
    hits = KB.search("cart RedisTimeoutException pool busy frontend 5xx", k=3)
    assert hits[0][0].id == "PM-2026-03-12"


def test_added_incidents_are_searchable():
    kb = KnowledgeBase()
    kb.add(Document(id="INC-1", title="checkout OOM", type="incident", body="checkout OOMKilled heap", services=["checkout"]))
    assert kb.search("checkout OOMKilled")[0][0].id == "INC-1"
