# -*- coding: utf-8 -*-
"""v2.40: worker concurrency + pool defaults."""
from app.core.config import settings
from app.services.agent_harness.model_driver import LoopState


def test_worker_max_concurrent_default():
    assert int(settings.WORKER_MAX_CONCURRENT) >= 2


def test_skill_explore_cap_tightened():
    assert LoopState.SKILL_EXPLORE_MAX <= 5
    assert LoopState.FETCH_TOOL_RESULT_MAX <= 3
    # v2.46：未动手时更早 soft-cap
    assert LoopState.SKILL_EXPLORE_SOFT_MAX <= LoopState.SKILL_EXPLORE_MAX
    assert LoopState.SKILL_EXPLORE_MAX <= 4
