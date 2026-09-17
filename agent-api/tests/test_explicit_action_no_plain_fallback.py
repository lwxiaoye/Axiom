from app.services.chat.main_tool_turn import requires_truthful_action_failure


def test_execute_and_revise_never_fall_back_to_no_tool_answer():
    assert requires_truthful_action_failure("execute")
    assert requires_truthful_action_failure("revise")


def test_conversation_may_use_plain_answer_fallback():
    assert not requires_truthful_action_failure("conversation")
