"""Evidence-based percentages derived from saved answers, never model-written totals."""

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

SCORE_WEIGHTS = {"professional": 50, "logic": 30, "expression": 20}


def _round(value: float, places: int = 2):
    result = Decimal(str(value)).quantize(Decimal(10) ** -places, rounding=ROUND_HALF_UP)
    return int(result) if places == 0 else float(result)


def summarize_performance(turns) -> tuple[dict, dict]:
    groups = {assisted: {key: defaultdict(list) for key in SCORE_WEIGHTS} for assisted in (False, True)}
    assessed_turns = {False: 0, True: 0}
    assessed_questions = {False: set(), True: set()}
    for turn in turns:
        if turn.action != "answer":
            continue
        evaluation = turn.evaluation_json or {}
        # Records written before the percentage contract have no scale marker.
        scale = evaluation.get("score_scale", 5)
        if scale not in (5, 100):
            continue
        assisted = bool(turn.assisted)
        root = (turn.question_json or {}).get("parent_question_id") or turn.question_id
        assessed = False
        for key in SCORE_WEIGHTS:
            item = (evaluation.get("dimensions") or {}).get(key) or {}
            score = item.get("score")
            if (item.get("status") != "scored" or type(score) is not int
                    or not (1 if scale == 5 else 0) <= score <= scale
                    or not item.get("evidence")):
                continue
            groups[assisted][key][root].append(score * 100 / scale)
            assessed = True
        if assessed:
            assessed_turns[assisted] += 1
            assessed_questions[assisted].add(root)

    averages = {False: {}, True: {}}
    summary = {}
    for key in SCORE_WEIGHTS:
        counts = {}
        for assisted in (False, True):
            answers = groups[assisted][key]
            # Average within a main question first so extra follow-ups cannot dominate.
            means = [sum(values) / len(values) for values in answers.values()]
            averages[assisted][key] = sum(means) / len(means) if means else None
            counts[assisted] = sum(len(values) for values in answers.values())
        summary[key] = {
            "average": _round(averages[False][key]) if counts[False] else None,
            "assessed_count": counts[False],
            "assisted_average": _round(averages[True][key]) if counts[True] else None,
            "assisted_count": counts[True],
        }

    def total(assisted):
        values = averages[assisted]
        if any(value is None for value in values.values()):
            return None
        return _round(sum(values[key] * weight for key, weight in SCORE_WEIGHTS.items()) / 100, 0)

    return summary, {
        "score": total(False), "assisted_score": total(True),
        "assessed_turns": assessed_turns[False], "assisted_turns": assessed_turns[True],
        "assessed_questions": len(assessed_questions[False]), "assisted_questions": len(assessed_questions[True]),
        "weights": dict(SCORE_WEIGHTS),
    }
