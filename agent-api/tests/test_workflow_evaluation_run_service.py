from app.services.workflows.evaluation_run_service import _safe_document, workflow_definition_hash


def test_evaluation_trace_redacts_credentials_and_bounds_long_text():
    value = _safe_document(
        {
            "Authorization": "Bearer top-secret",
            "nested": {"apiKey": "also-secret", "visible": "ok"},
            "long": "x" * 40_000,
        },
        {},
    )

    assert value["Authorization"] == "[已脱敏]"
    assert value["nested"]["apiKey"] == "[已脱敏]"
    assert value["nested"]["visible"] == "ok"
    assert value["long"].endswith("[内容截断]")


def test_workflow_definition_hash_is_stable_and_content_sensitive():
    assert workflow_definition_hash('{"nodes": []}') == workflow_definition_hash('{ "nodes" : [] }')
    assert workflow_definition_hash('{"nodes":[]}') != workflow_definition_hash('{"nodes":[1]}')
