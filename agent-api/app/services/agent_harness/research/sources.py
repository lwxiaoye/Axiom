"""Evidence readiness and a bounded retry within the team's frozen scope."""
from .engine import synthesis_sources


def has_usable_evidence(ledger) -> bool:
    return any(source.snippet.strip() for source in synthesis_sources(ledger))


def recovery_queries(team) -> list[dict[str, str]]:
    queries, seen = [], set()
    for member in team.get("members", []):
        for receipt in member.get("searches", []):
            query = str(receipt.get("query") or "").strip()
            if (not query or query.startswith(("http://", "https://")) or query in seen
                    or receipt.get("count") or receipt.get("phase", "findings") != "findings"):
                continue
            topic_id = receipt.get("topic_id")
            if topic_id not in (member.get("topicIds") or team.get("topicIds", [])):
                continue
            queries.append({"memberId": member["id"], "query": query, "topic_id": topic_id})
            seen.add(query)
            # One exact query per member, at most two queries for the entire Run.
            break
        if len(queries) == 2:
            break
    return queries


def search_was_unavailable(ledger, team) -> bool:
    return bool(ledger.search_failures) or any(
        receipt.get("providerFailures") or receipt.get("status") in {"failed", "interrupted"}
        for member in (team or {}).get("members", [])
        for receipt in member.get("searches", [])
    )
