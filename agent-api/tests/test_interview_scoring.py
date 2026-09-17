"""Aggregate contract for evidence, scale upgrades, follow-ups and practice attempts."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.services.chat.builtin_assistants.interview.contracts import DimensionScore, InterviewEvaluation
from app.services.chat.builtin_assistants.interview.scoring import summarize_performance


def answer(root, scores=(80, 70, 60), *, scale=100, assisted=False, parent=None):
    dimensions = {key: {"status": "scored" if score is not None else "insufficient_evidence", "score": score,
                        "reason": "依据实际回答", "evidence": [{"message_id": 1, "quote": "真实回答"}] if score is not None else []}
                  for key, score in zip(("professional", "logic", "expression"), scores)}
    evaluation = {"dimensions": dimensions, "feedback": "补充验证条件。"}
    if scale is not None:
        evaluation["score_scale"] = scale
    return SimpleNamespace(action="answer", question_id=root, question_json={"parent_question_id": parent},
                           assisted=assisted, evaluation_json=evaluation)


def test_aggregate_uses_all_roots_and_followups_without_overweighting_one_topic():
    turns = [answer("q1", (20, 40, 60)), answer("q1f1", (100, 100, 100), parent="q1"),
             answer("q1f2", (60, 70, 80), parent="q1"), answer("q2", (80, 90, 100))]
    summary, performance = summarize_performance(turns)
    assert [summary[key]["average"] for key in ("professional", "logic", "expression")] == [70, 80, 90]
    assert performance == {"score": 77, "assisted_score": None, "assessed_turns": 4,
                           "assisted_turns": 0, "assessed_questions": 2, "assisted_questions": 0,
                           "weights": {"professional": 50, "logic": 30, "expression": 20}}


def test_mixed_legacy_and_percentage_records_do_not_rewrite_original_or_misread_low_scores():
    turns = [answer("q1", (4, 3, 5), scale=None), answer("q2", (4, 0, 100))]
    original = deepcopy(turns)
    summary, performance = summarize_performance(turns)
    assert [summary[key]["average"] for key in ("professional", "logic", "expression")] == [42, 30, 100]
    assert performance["score"] == 50
    assert [vars(turn) for turn in turns] == [vars(turn) for turn in original]


def test_practice_is_separate_from_original_and_missing_evidence_cannot_be_zero_or_reweighted():
    first = answer("q1", (None, 60, 70))
    practice = answer("q1", (90, 80, 70), assisted=True)
    summary, performance = summarize_performance([first, practice])
    assert summary["professional"]["average"] is None
    assert performance["score"] is None
    assert performance["assisted_score"] == 83
    assert performance["assisted_turns"] == 1


@pytest.mark.parametrize("invalid", [True, -1, 101, 70.5, "80"])
def test_invalid_stored_values_never_enter_the_aggregate(invalid):
    turn = answer("q1", (invalid, 70, 80))
    summary, performance = summarize_performance([turn])
    assert summary["professional"]["average"] is None
    assert performance["score"] is None


def test_unanswered_and_unproven_scores_do_not_produce_a_total():
    skipped = answer("q1")
    skipped.action = "skip"
    unsupported = answer("q2")
    for dimension in unsupported.evaluation_json["dimensions"].values():
        dimension["evidence"] = []
    summary, performance = summarize_performance([skipped, unsupported])
    assert all(item["average"] is None for item in summary.values())
    assert performance["score"] is None and performance["assessed_turns"] == 0


@pytest.mark.parametrize("score", [0, 4, 68, 100])
def test_native_percentage_contract_preserves_actual_integers(score):
    dimension = DimensionScore(status="scored", score=score, reason="依据回答", evidence=[{"message_id": 1, "quote": "真实回答"}])
    assert dimension.score == score
    payload = answer("q1", (score, score, score)).evaluation_json
    assert InterviewEvaluation.model_validate(payload).score_scale == 100


def test_new_submissions_reject_legacy_scale_and_out_of_range_values():
    with pytest.raises(ValidationError):
        InterviewEvaluation.model_validate(answer("q1", scale=5).evaluation_json)
    for score in (-1, 101, True, 4.5):
        with pytest.raises(ValidationError):
            InterviewEvaluation.model_validate(answer("q1", (score, 80, 80)).evaluation_json)
