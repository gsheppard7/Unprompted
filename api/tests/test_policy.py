from app.cognition import extract_numbers, propose
from app.eval import evaluate
from app.policy import utility_of


def test_season_strings_are_not_stats():
    assert extract_numbers("the 2025-26 season") == []
    assert extract_numbers("averaged 9.1 assists") == [9.1]
    assert extract_numbers("averaged five assists") == [5.0]


def test_grounded_correction_clears_the_conservative_bar():
    report = evaluate()
    row = next(item for item in report["rows"] if item["name"] == "grounded correction")
    assert row["ours"] == "speak"
    assert row["status"] == "speak"
    assert row["utility"] >= 0.72


def test_policy_beats_chatty_baselines_on_the_labeled_set():
    report = evaluate()
    assert report["ours"]["precision"] == 1
    assert report["ours"]["recall"] == 1
    assert report["always"]["precision"] < report["ours"]["precision"]
    assert report["ungated"]["precision"] < report["ours"]["precision"]
    assert report["direct"]["recall"] < report["ours"]["recall"]
    for row in report["rows"]:
        assert row["ours"] == row["expect"], row


def test_withheld_disagreement_still_has_a_draft():
    proposal = propose(
        "user.message",
        [
            {"role": "user", "initiator": "user", "text": "He was the best."},
            {"role": "user", "initiator": "user", "text": "No way, that's wrong."},
        ],
        [],
    )
    assert proposal.kind == "clarification"
    assert proposal.draft
    assert utility_of(proposal) < 0.55
