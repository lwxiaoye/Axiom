from app.services.skills.skill_package_bridge import validate_harness_skill_package


def test_current_harness_skill_contract_is_accepted():
    files = {
        "SKILL.md": b"Use bash and save deliverables under /workspace/files.",
        "scripts/build.py": b"print('ok')",
    }
    assert validate_harness_skill_package(files) == ()


def test_retired_tool_or_workspace_contract_is_rejected():
    retired_tool = "run" + "_code"
    files = {
        "SKILL.md": f"Call {retired_tool} and write /workspace/outputs/report.pdf".encode(),
    }
    violations = validate_harness_skill_package(files)
    assert retired_tool in violations
    assert "/workspace/outputs" in violations
