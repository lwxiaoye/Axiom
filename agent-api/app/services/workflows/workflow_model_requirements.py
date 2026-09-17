"""Helpers for the explicit chat-model requirements in a workflow definition."""
import json
from typing import Any, Iterable


def parse_required_model_ids(value: Any) -> list[str]:
    """Normalize persisted model IDs while retaining their first-seen order."""
    items: Iterable[Any]
    if value is None:
        items = []
    elif isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError:
                decoded = None
            items = decoded if isinstance(decoded, list) else value.split(",")
        else:
            items = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = [value]

    result: list[str] = []
    for item in items:
        if isinstance(item, dict):
            item = item.get("id") or item.get("value")
        if item is None:
            continue
        model_id = str(item).strip()
        if not model_id or model_id.lower() == "default" or model_id in result:
            continue
        result.append(model_id)
    return result


def _parse_workflow_json(workflow_json: Any) -> dict:
    if isinstance(workflow_json, str):
        try:
            workflow_json = json.loads(workflow_json)
        except (TypeError, json.JSONDecodeError):
            return {}
    return workflow_json if isinstance(workflow_json, dict) else {}


def _iter_workflow_nodes(workflow_json: dict) -> Iterable[Any]:
    nodes = workflow_json.get("nodes", [])
    if isinstance(nodes, list):
        yield from nodes
    fastgpt = workflow_json.get("fastgpt")
    if isinstance(fastgpt, dict) and isinstance(fastgpt.get("nodes"), list):
        yield from fastgpt["nodes"]


def parse_required_knowledge_ids(value: Any) -> list[str]:
    """Normalize persisted knowledge IDs while retaining their first-seen order."""
    result: list[str] = []

    def add(item: Any) -> None:
        if isinstance(item, (list, tuple, set)):
            for child in item:
                add(child)
            return
        if isinstance(item, dict):
            item = item.get("datasetId") or item.get("id") or item.get("knowledgeId")
        if item is None:
            return
        knowledge_id = str(item).strip()
        if knowledge_id and knowledge_id not in result:
            result.append(knowledge_id)

    add(value)
    return result


def extract_required_knowledge_ids(workflow_json: Any) -> list[str]:
    """Return knowledge base IDs referenced by chat, search, and agent nodes."""
    workflow_json = _parse_workflow_json(workflow_json)
    if not workflow_json:
        return []

    values: list[Any] = []

    def collect_dataset_params(value: Any) -> None:
        if isinstance(value, dict):
            values.append(value.get("datasets"))

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            inputs = value.get("inputs")
            if isinstance(inputs, list):
                for input_item in inputs:
                    if not isinstance(input_item, dict):
                        continue
                    key = input_item.get("key")
                    if key in {"aiChatDatasets", "datasets"}:
                        values.append(input_item.get("value"))
                    elif key in {"agent_datasetParams", "datasetParams"}:
                        collect_dataset_params(input_item.get("value"))
            elif isinstance(inputs, dict):
                values.append(inputs.get("aiChatDatasets"))
                values.append(inputs.get("datasets"))
                collect_dataset_params(inputs.get("agent_datasetParams"))
                collect_dataset_params(inputs.get("datasetParams"))
            for child in value.values():
                if isinstance(child, (dict, list)):
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                if isinstance(child, (dict, list)):
                    visit(child)

    visit(list(_iter_workflow_nodes(workflow_json)))
    return parse_required_knowledge_ids(values)


def extract_required_model_ids(workflow_json: Any) -> list[str]:
    """Return explicit ``inputs[].key == 'model'`` values from all workflow nodes."""
    workflow_json = _parse_workflow_json(workflow_json)
    if not workflow_json:
        return []

    values: list[Any] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            inputs = value.get("inputs")
            if isinstance(inputs, list):
                for input_item in inputs:
                    if isinstance(input_item, dict) and input_item.get("key") == "model":
                        values.append(input_item.get("value"))
            elif isinstance(inputs, dict) and "model" in inputs:
                values.append(inputs["model"])
            for child in value.values():
                if isinstance(child, (dict, list)):
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                if isinstance(child, (dict, list)):
                    visit(child)

    visit(list(_iter_workflow_nodes(workflow_json)))
    return parse_required_model_ids(values)


def extract_referenced_app_ids(workflow_json: Any) -> list[str]:
    """Return app IDs referenced by appModule/pluginModule nodes in first-seen order."""
    workflow_json = _parse_workflow_json(workflow_json)
    if not workflow_json:
        return []

    result: list[str] = []
    for node in _iter_workflow_nodes(workflow_json):
        if not isinstance(node, dict):
            continue
        if node.get("flowNodeType") not in {"appModule", "pluginModule"}:
            continue
        app_id = str(node.get("pluginId") or "").strip()
        if app_id and app_id not in result:
            result.append(app_id)
    return result


def missing_required_models(required: Any, available: Any) -> list[str]:
    """Return required model IDs unavailable to the current user."""
    available_ids = set(parse_required_model_ids(available))
    return [model_id for model_id in parse_required_model_ids(required) if model_id not in available_ids]
