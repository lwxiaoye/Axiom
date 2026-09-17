from pathlib import Path


def test_list_recent_file_names_exists_in_service():
    src = Path(__file__).resolve().parents[1].joinpath(
        "app/services/files/user_file_service.py"
    ).read_text(encoding="utf-8")
    assert "async def list_recent_file_names" in src
    # build_resume_checkpoint uses the scoped recovery path, never the user-global recent index.
    ctx = Path(__file__).resolve().parents[1].joinpath(
        "app/services/chat/turn_context_builder.py"
    ).read_text(encoding="utf-8")
    assert "list_resume_file_names" in ctx
