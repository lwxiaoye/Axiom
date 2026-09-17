"""Frozen task scope shared by planning, member tools and report writing."""
from __future__ import annotations


def freeze_scope(team, topics, args):
    ids = [topic.topic_id for topic in topics]
    previous = team.get("scope")
    if previous:
        if ids != previous["topicIds"]:
            raise ValueError("取证范围已确定；请在原主题内解决关键缺口，不得新增研究支线。")
        return previous
    basis = str(args.get("breadth_evidence") or "").strip()
    if len(ids) > 3 and not team.get("topicIds"):
        if len(ids) > 5 or len(basis) < 8 or basis not in team["query"]:
            raise ValueError("一般研究请合并为 2–3 个核心主题；最多 5 项的多维研究须提供用户要求扩大范围的原话。")
    raw = args.get("scope") if isinstance(args.get("scope"), dict) else {}
    excluded = raw.get("excluded") if isinstance(raw.get("excluded"), list) else []
    return {"topicIds": ids, "focus": str(raw.get("focus") or team["query"])[:600],
            "excluded": [str(value)[:200] for value in excluded[:5]], "breadthEvidence": basis[:600]}


def assign_topics(team):
    """The coordinator does not run a fourth search pass. Ownership survives resume."""
    members = team["members"]
    ids = team["topicIds"]
    for index, member in enumerate(members):
        if not member.get("topicIds"):
            # An extra verifier checks an existing question, not a new branch.
            member["topicIds"] = ids[index::len(members)] or ids[:1]


def member_topics(team, member, phase):
    if phase.startswith("supplement_"):
        ids = [row["topic_id"] for row in member.get("assignments", [])]
    else:
        ids = member.get("topicIds") or team["topicIds"]
    return [topic for topic in team["topics"] if topic["id"] in ids]
