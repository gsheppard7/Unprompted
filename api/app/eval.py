"""Tiny labeled set and three baselines.

Run from api/:  python -m app.eval
"""

from __future__ import annotations

from app.cognition import propose
from app.policy import decide, utility_of

FACT = {
    "id": "mem-jordan",
    "kind": "fact",
    "content": "Jordan Hale averaged 9.1 assists per game in the 2025-26 season.",
    "source_label": "demo ledger",
    "verified": True,
}
UNVERIFIED = {**FACT, "id": "mem-unverified", "verified": False, "source_label": "someone said"}

SCENARIOS: list[dict] = [
    {
        "name": "grounded correction",
        "mode": "conservative",
        "event": "user.message",
        "expect": "speak",
        "messages": [
            {"role": "user", "initiator": "user", "text": "I think Jordan Hale was the best passer in the league last year."},
            {"role": "user", "initiator": "user", "text": "No way, he barely averaged 5 assists."},
        ],
        "memories": [FACT],
    },
    {
        "name": "number word five",
        "mode": "conservative",
        "event": "user.message",
        "expect": "speak",
        "messages": [
            {"role": "user", "initiator": "user", "text": "Jordan Hale was unbelievable last year."},
            {"role": "user", "initiator": "user", "text": "No way, he barely averaged five assists."},
        ],
        "memories": [FACT],
    },
    {
        "name": "idle open question",
        "mode": "balanced",
        "event": "session.idle",
        "expect": "speak",
        "messages": [
            {"role": "user", "initiator": "user", "text": "I wonder what Jordan Hale averaged last season."},
        ],
        "memories": [FACT],
    },
    {
        "name": "idle on conservative",
        "mode": "conservative",
        "event": "session.idle",
        "expect": "silent",
        "messages": [
            {"role": "user", "initiator": "user", "text": "I wonder what Jordan Hale averaged last season."},
        ],
        "memories": [FACT],
    },
    {
        "name": "opinion only",
        "mode": "conservative",
        "event": "user.message",
        "expect": "silent",
        "messages": [
            {"role": "user", "initiator": "user", "text": "I think Jordan Hale was the best passer in the league last year."},
        ],
        "memories": [FACT],
    },
    {
        "name": "small talk",
        "mode": "balanced",
        "event": "user.message",
        "expect": "silent",
        "messages": [
            {"role": "user", "initiator": "user", "text": "haha yeah the weather is nice today"},
        ],
        "memories": [FACT],
    },
    {
        "name": "dispute without a source",
        "mode": "balanced",
        "event": "user.message",
        "expect": "silent",
        "messages": [
            {"role": "user", "initiator": "user", "text": "He was the best passer."},
            {"role": "user", "initiator": "user", "text": "No way, that's wrong."},
        ],
        "memories": [],
    },
    {
        "name": "unverified conflict",
        "mode": "balanced",
        "event": "user.message",
        "expect": "silent",
        "messages": [
            {"role": "user", "initiator": "user", "text": "Jordan Hale averaged 5 assists."},
        ],
        "memories": [UNVERIFIED],
    },
    {
        "name": "proactivity off",
        "mode": "off",
        "event": "user.message",
        "expect": "silent",
        "messages": [
            {"role": "user", "initiator": "user", "text": "Jordan Hale averaged 5 assists."},
        ],
        "memories": [FACT],
    },
    {
        "name": "repeat correction",
        "mode": "conservative",
        "event": "user.message",
        "expect": "silent",
        "repeat": True,
        "messages": [
            {"role": "user", "initiator": "user", "text": "Jordan Hale averaged 5 assists."},
        ],
        "memories": [FACT],
    },
    {
        "name": "user is typing",
        "mode": "conservative",
        "event": "user.message",
        "expect": "silent",
        "typing": True,
        "messages": [
            {"role": "user", "initiator": "user", "text": "Jordan Hale averaged 5 assists."},
        ],
        "memories": [FACT],
    },
]


def run_ours(scenario: dict) -> tuple[str, str, float]:
    messages = list(scenario["messages"])
    memories = scenario["memories"]
    if scenario.get("repeat"):
        first = propose(scenario["event"], messages, memories)
        messages = messages + [
            {"role": "assistant", "initiator": "proactive", "text": first.draft}
        ]
    proposal = propose(scenario["event"], messages, memories)
    decision = decide(
        proposal,
        mode=scenario["mode"],
        typing=bool(scenario.get("typing")),
        budget_remaining=int(scenario.get("budget_remaining", 3)),
    )
    label = "speak" if decision.status == "speak" else "silent"
    return label, decision.status, utility_of(proposal)


def baseline_direct(_scenario: dict) -> str:
    return "silent"


def baseline_always(scenario: dict) -> str:
    if scenario["mode"] == "off" or not scenario["messages"]:
        return "silent"
    return "speak"


def baseline_ungated(scenario: dict) -> str:
    """Score every turn and speak on a low bar, without the evidence gate."""
    if scenario["mode"] == "off":
        return "silent"
    proposal = propose(scenario["event"], scenario["messages"], scenario["memories"])
    if proposal.draft and utility_of(proposal) >= 0.35 and proposal.kind not in {"none", "connection"}:
        return "speak"
    return "silent"


def precision_recall(pairs: list[tuple[str, str]]) -> dict[str, float]:
    true_positive = sum(predicted == "speak" and expected == "speak" for predicted, expected in pairs)
    false_positive = sum(predicted == "speak" and expected == "silent" for predicted, expected in pairs)
    false_negative = sum(predicted == "silent" and expected == "speak" for predicted, expected in pairs)
    precision = true_positive / (true_positive + false_positive) if (true_positive + false_positive) else 1.0
    recall = true_positive / (true_positive + false_negative) if (true_positive + false_negative) else 1.0
    return {"precision": precision, "recall": recall, "spoke": true_positive + false_positive}


def evaluate() -> dict:
    rows = []
    ours_pairs = []
    direct_pairs = []
    always_pairs = []
    ungated_pairs = []
    for scenario in SCENARIOS:
        expected = scenario["expect"]
        ours, status, utility = run_ours(scenario)
        direct = baseline_direct(scenario)
        always = baseline_always(scenario)
        ungated = baseline_ungated(scenario)
        rows.append(
            {
                "name": scenario["name"],
                "expect": expected,
                "ours": ours,
                "status": status,
                "utility": round(utility, 3),
                "direct": direct,
                "always": always,
                "ungated": ungated,
            }
        )
        ours_pairs.append((ours, expected))
        direct_pairs.append((direct, expected))
        always_pairs.append((always, expected))
        ungated_pairs.append((ungated, expected))
    return {
        "rows": rows,
        "ours": precision_recall(ours_pairs),
        "direct": precision_recall(direct_pairs),
        "always": precision_recall(always_pairs),
        "ungated": precision_recall(ungated_pairs),
    }


def main() -> None:
    report = evaluate()
    header = f"{'scenario':<24} {'expect':<8} {'ours':<8} {'status':<12} {'util':<6} {'always':<8} {'ungated':<8}"
    print(header)
    print("-" * len(header))
    for row in report["rows"]:
        print(
            f"{row['name']:<24} {row['expect']:<8} {row['ours']:<8} {row['status']:<12} "
            f"{row['utility']:<6} {row['always']:<8} {row['ungated']:<8}"
        )
    print()
    for name in ("ours", "direct", "always", "ungated"):
        metrics = report[name]
        print(
            f"{name:<8} precision {metrics['precision']:.2f}  "
            f"recall {metrics['recall']:.2f}  spoke {metrics['spoke']}"
        )


if __name__ == "__main__":
    main()
