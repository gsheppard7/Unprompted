"""Transparent speak / stay-silent policy.

Utility = 0.30 relevance + 0.25 evidence + 0.20 novelty + 0.15 urgency
          - 0.10 interruption_cost

Weights are a starting hypothesis, not a fitted model. Thresholds prefer
missing a chance to speak over interrupting without a reason.
"""

from __future__ import annotations

from dataclasses import dataclass


MODES: dict[str, dict[str, float]] = {
    "off": {
        "threshold": 1.0,
        "hourly_budget": 0,
        "idle_s": 10**9,
        "min_evidence": 1.0,
    },
    "conservative": {
        "threshold": 0.72,
        "hourly_budget": 3,
        "idle_s": 22,
        "min_evidence": 0.70,
    },
    "balanced": {
        "threshold": 0.55,
        "hourly_budget": 8,
        "idle_s": 10,
        "min_evidence": 0.50,
    },
}


@dataclass
class Proposal:
    kind: str
    draft: str
    relevance: float
    evidence: float
    novelty: float
    urgency: float
    interruption_cost: float
    reason: str
    memory_ids: list[str]


@dataclass
class Decision:
    status: str
    reason: str
    utility: float


def utility_of(proposal: Proposal) -> float:
    return (
        0.30 * proposal.relevance
        + 0.25 * proposal.evidence
        + 0.20 * proposal.novelty
        + 0.15 * proposal.urgency
        - 0.10 * proposal.interruption_cost
    )


def decide(
    proposal: Proposal,
    *,
    mode: str,
    typing: bool = False,
    busy: bool = False,
    budget_remaining: int = 1,
    reserved: bool = False,
) -> Decision:
    """Choose ignore, save_only, deferred, rejected, or speak."""
    utility = utility_of(proposal)
    if mode == "off":
        return Decision("rejected", "Proactivity is off.", utility)
    if reserved:
        return Decision("deferred", "Another contribution is already queued.", utility)
    if busy:
        return Decision("deferred", "A reply is already in progress.", utility)
    if typing:
        return Decision("deferred", "You're still writing, so this waits.", utility)
    if budget_remaining <= 0:
        return Decision("rejected", "Hourly interruption budget is spent.", utility)
    if proposal.novelty < 0.25 and proposal.kind not in {"none", "connection"}:
        return Decision("rejected", "Already said something very similar.", utility)

    config = MODES[mode]
    if proposal.kind == "correction" and proposal.evidence < config["min_evidence"]:
        return Decision("save_only", proposal.reason, utility)
    if not proposal.draft or utility < config["threshold"]:
        if utility >= 0.35 and proposal.kind not in {"none", "connection"} and proposal.draft:
            return Decision("save_only", proposal.reason, utility)
        return Decision("ignored", proposal.reason, utility)
    return Decision("speak", proposal.reason, utility)
