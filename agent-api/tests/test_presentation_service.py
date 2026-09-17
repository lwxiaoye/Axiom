import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.auth import UserContext
from app.services.workflows.presentation_service import (
    BUILTIN_PRESENTATION_PRESETS,
    asset_inventory_problem,
    extract_presentation_config,
    validate_definition_for_app,
)
from app.services.workflows.sub_agent_skin_service import assignment_key


def test_presentation_config_allows_only_declarative_key_and_safe_copy():
    payload = json.dumps({
        "chatConfig": {
            "presentation": {
                "schemaVersion": 1,
                "preset": "campus-welcome-v1",
                "copy": {"welcomeTitle": "欢迎新同学"},
            }
        }
    }, ensure_ascii=False)

    assert extract_presentation_config(payload) == {
        "schemaVersion": 1,
        "preset": "campus-welcome-v1",
        "copy": {"welcomeTitle": "欢迎新同学"},
    }


def test_presentation_config_reads_legacy_fastgpt_envelope():
    payload = json.dumps({
        "version": "2.1.0",
        "fastgpt": {
            "nodes": [{"nodeId": "n1"}],
            "chatConfig": {
                "presentation": {"schemaVersion": 1, "preset": "campus-welcome-v1"}
            },
        },
    })

    assert extract_presentation_config(payload)["preset"] == "campus-welcome-v1"


@pytest.mark.parametrize("unsafe_field", ["css", "html", "backgroundUrl", "component"])
def test_presentation_config_rejects_executable_or_remote_style_fields(unsafe_field):
    payload = json.dumps({
        "chatConfig": {
            "presentation": {
                "schemaVersion": 1,
                "preset": "campus-welcome-v1",
                unsafe_field: "https://example.invalid/unsafe",
            }
        }
    })

    with pytest.raises(HTTPException) as exc:
        extract_presentation_config(payload)

    assert exc.value.status_code == 400


def test_builtin_asset_inventory_checks_owner_reference_and_checksum():
    spec = BUILTIN_PRESENTATION_PRESETS["campus-welcome-v1"]
    preset = SimpleNamespace()
    assets = [
        SimpleNamespace(
            asset_key=key,
            resource_ref=value.resource_ref,
            sha256=value.sha256,
            status="active",
        )
        for key, value in spec.assets.items()
    ]

    assert asset_inventory_problem(spec, preset, assets) == ""
    assets[0].sha256 = "0" * 64
    assert "校验值" in asset_inventory_problem(spec, preset, assets)


@pytest.mark.asyncio
async def test_cross_tenant_editor_cannot_assign_customer_presentation():
    app = SimpleNamespace(id="app-1", tenant_id="tenant-a")
    user = UserContext(user_id="u1", username="editor", tenant_id="tenant-b")

    with pytest.raises(HTTPException) as exc:
        await validate_definition_for_app(None, app, json.dumps({"chatConfig": {}}), user=user)

    assert exc.value.status_code == 403


def test_portable_assignment_key_is_deterministic_and_fits_legacy_columns():
    short = assignment_key("campus-welcome-portable", "1.2.3")
    long = assignment_key("a" * 64, "1234567890.1234567890.1234567890")

    assert short == "skin-campus-welcome-portable-v1-2-3"
    assert long == assignment_key("a" * 64, "1234567890.1234567890.1234567890")
    assert len(long) <= 64


def test_fitting_room_catalog_does_not_inject_code_presets_or_deleted_packages():
    import inspect

    from app.services.workflows import presentation_service

    src = inspect.getsource(presentation_service.list_available_presets)
    assert "BUILTIN_PRESENTATION_PRESETS" not in src
    assert "list_skins" in src
