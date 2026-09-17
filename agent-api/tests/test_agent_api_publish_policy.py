import pytest
from fastapi import HTTPException

from app.services.agent_api.publish_policy import (
    ApiPublishCapabilityError,
    normalize_embed_origins,
    normalize_publish_channels,
    validate_api_workflow_capabilities,
)


def _workflow(*nodes: dict) -> str:
    import json

    return json.dumps({"fastgpt": {"nodes": list(nodes), "edges": []}})


def test_publish_channels_are_deduplicated_and_must_be_known():
    assert normalize_publish_channels([" api ", "marketplace", "api"]) == ("api", "marketplace")

    with pytest.raises(HTTPException, match="发布通道"):
        normalize_publish_channels([])
    with pytest.raises(HTTPException, match="发布通道"):
        normalize_publish_channels(["partner"])


def test_embed_origins_are_exact_https_origins_only():
    assert normalize_embed_origins(["https://Portal.Example.com:443", "https://portal.example.com"]) == (
        "https://portal.example.com",
    )

    for invalid in (
        "http://portal.example.com",
        "https://*.example.com",
        "https://portal.example.com/embed",
        "https://portal.example.com?tenant=1",
    ):
        with pytest.raises(HTTPException, match="嵌入来源"):
            normalize_embed_origins([invalid])


def test_api_channel_accepts_files_and_hitl_once_external_context_is_ready():
    validate_api_workflow_capabilities(
        _workflow({"nodeId": "ask", "flowNodeType": "userSelect", "inputs": []}),
        external_context_ready=True,
    )
    validate_api_workflow_capabilities(
        _workflow({"nodeId": "files", "flowNodeType": "readFiles", "inputs": []}),
        external_context_ready=True,
    )


def test_api_channel_accepts_publisher_authorized_browser_and_oauth_capabilities():
    validate_api_workflow_capabilities(
        _workflow({"nodeId": "tool", "flowNodeType": "tool", "toolConfig": {"type": "browser"}}),
        external_context_ready=True,
    )
    validate_api_workflow_capabilities(
        _workflow({"nodeId": "tool", "flowNodeType": "tool", "toolConfig": {"authType": "oauth"}}),
        external_context_ready=True,
    )


def test_marketplace_only_does_not_apply_api_capability_validation():
    assert normalize_publish_channels(["marketplace"]) == ("marketplace",)
