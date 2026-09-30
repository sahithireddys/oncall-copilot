"""Claude reasoner: evidence bundle -> structured `Hypothesis` via LangChain + Anthropic."""
from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from ..knowledge.store import KnowledgeBase
from ..models import (
    Alert, AlternativeHypothesis, Evidence, EvidenceRef, Finding, Hypothesis, RootCauseCategory, SuggestedAction,
)
from ..topology import Topology
from .prompts import SYSTEM_PROMPT, build_user_prompt

log = logging.getLogger(__name__)


class RootCauseReport(BaseModel):
    """Root-cause hypothesis for the on-call engineer."""
    reasoning: str = Field(description="Step-by-step reasoning from alert to root cause, including why red herrings were dismissed. 3-8 sentences.")
    root_cause_service: str = Field(description="Service where the fault originates (a node of the call graph).")
    category: RootCauseCategory
    title: str = Field(description="One line, e.g. 'payment 1.9.1 rollout throws TypeError on charge'.")
    summary: str = Field(description="2-4 sentences: the causal chain from root cause to the alert, for a Slack message.")
    confidence: float = Field(ge=0, le=1)
    supporting_evidence: list[EvidenceRef] = Field(description="3-6 evidence ids from the list, each with why it matters.")
    suggested_actions: list[SuggestedAction] = Field(description="1-3 actions, safest mitigation first.")
    related_documents: list[str] = Field(default_factory=list, description="Ids of matching postmortems/runbooks.")
    alternatives: list[AlternativeHypothesis] = Field(default_factory=list, description="0-2 runner-up explanations.")


class ClaudeReasoner:
    def __init__(self, api_key: str, model: str, max_tokens: int = 2500, temperature: float = 0.0):
        from langchain_anthropic import ChatAnthropic  # imported lazily so heuristic mode needs no API deps

        self.model_name = model
        self.chat = ChatAnthropic(model=model, api_key=api_key, max_tokens=max_tokens, temperature=temperature,
                                  timeout=90, max_retries=2)
        self.structured = self.chat.with_structured_output(RootCauseReport)
        self.last_prompt: str | None = None
        self.last_reasoning: str | None = None

    def hypothesize(self, alert: Alert, evidence: list[Evidence], findings: list[Finding], documents: list[dict],
                    topology: Topology, kb: KnowledgeBase | None = None) -> Hypothesis:
        user = build_user_prompt(alert, evidence, findings, documents, topology)
        self.last_prompt = user
        report: RootCauseReport = self.structured.invoke([("system", SYSTEM_PROMPT), ("human", user)])
        if report is None:
            raise RuntimeError("model returned no structured output")
        self.last_reasoning = report.reasoning
        known_docs = {d["id"] for d in documents}
        if kb:
            known_docs |= set(kb.docs)
        return Hypothesis(
            root_cause_service=report.root_cause_service.strip(),
            category=report.category,
            title=report.title,
            summary=report.summary,
            reasoning=report.reasoning,
            confidence=report.confidence,
            supporting_evidence=report.supporting_evidence,
            suggested_actions=report.suggested_actions,
            related_documents=[d for d in report.related_documents if d in known_docs],
            alternatives=report.alternatives,
            reasoner=f"claude:{self.model_name}",
        )
