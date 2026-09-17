"""Research Profile controller: stage machine, evidence ledger, report compiler,
and the Research-only coverage kernel.
"""

from .contracts import (
    MIN_SOURCES_PER_TOPIC,
    ResearchLedger,
    ResearchStage,
    ResearchTopic,
    SourceRecord,
)
from .engine import (
    inherit_research_ledger,
    ingest_tool_receipt,
    research_prompt_suffix,
    seed_research_state,
)
from .kernel import run as run_research_turn
from .plan import default_topics, rewrite_queries
from .report import compile_report

__all__ = [
    "MIN_SOURCES_PER_TOPIC",
    "ResearchLedger",
    "ResearchStage",
    "ResearchTopic",
    "SourceRecord",
    "compile_report",
    "default_topics",
    "inherit_research_ledger",
    "ingest_tool_receipt",
    "research_prompt_suffix",
    "rewrite_queries",
    "run_research_turn",
    "seed_research_state",
]
