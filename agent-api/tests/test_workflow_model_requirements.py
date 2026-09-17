import json
import unittest

from app.services.workflows.workflow_model_requirements import (
    extract_required_knowledge_ids,
    extract_required_model_ids,
    missing_required_models,
    parse_required_model_ids,
)


class WorkflowModelRequirementsTest(unittest.TestCase):
    def test_extracts_explicit_node_models_in_first_seen_order(self):
        workflow = {
            "nodes": [
                {"inputs": [{"key": "model", "value": " gpt-4o "}, {"key": "model", "value": "default"}]},
                {"inputs": [{"key": "model", "value": "gpt-4o-mini"}]},
                {"inputs": [{"key": "model", "value": "gpt-4o"}]},
                {"inputs": [{"key": "not-model", "value": "ignored"}, {"key": "model", "value": ""}]},
            ]
        }

        self.assertEqual(extract_required_model_ids(json.dumps(workflow)), ["gpt-4o", "gpt-4o-mini"])

    def test_extracts_explicit_models_from_production_fastgpt_envelope(self):
        workflow = {
            "version": "1.0",
            "fastgpt": {
                "nodes": [
                    {"inputs": [{"key": "model", "value": " gpt-4o "}]},
                    {"inputs": [{"key": "model", "value": "default"}]},
                    {"inputs": [{"key": "model", "value": "gpt-4o-mini"}]},
                    {"inputs": [{"key": "model", "value": "gpt-4o"}]},
                ]
            },
        }

        self.assertEqual(extract_required_model_ids(json.dumps(workflow)), ["gpt-4o", "gpt-4o-mini"])

    def test_extracts_required_knowledge_ids_from_chat_search_and_agent_nodes(self):
        workflow = {
            "version": "1.0",
            "fastgpt": {
                "nodes": [
                    {
                        "inputs": [
                            {
                                "key": "aiChatDatasets",
                                "value": [
                                    {"datasetId": " kb-chat ", "name": "chat kb"},
                                    {"id": "kb-shared"},
                                ],
                            }
                        ]
                    },
                    {
                        "inputs": [
                            {
                                "key": "datasets",
                                "value": [{"datasetId": "kb-search"}, "kb-shared", "", None],
                            }
                        ]
                    },
                    {
                        "inputs": [
                            {
                                "key": "agent_datasetParams",
                                "value": {"datasets": [{"datasetId": "kb-agent"}, {"id": "kb-search"}]},
                            }
                        ]
                    },
                    {
                        "inputs": [
                            {
                                "key": "datasetParams",
                                "value": {"datasets": ["kb-legacy"]},
                            }
                        ]
                    },
                ]
            },
        }

        self.assertEqual(
            extract_required_knowledge_ids(json.dumps(workflow)),
            ["kb-chat", "kb-shared", "kb-search", "kb-agent", "kb-legacy"],
        )

    def test_parse_required_model_ids_normalizes_csv_json_and_defaults(self):
        self.assertEqual(
            parse_required_model_ids(' gpt-4o, default, gpt-4o-mini, gpt-4o '),
            ["gpt-4o", "gpt-4o-mini"],
        )
        self.assertEqual(parse_required_model_ids('["gpt-4o", "", "default"]'), ["gpt-4o"])

    def test_returns_missing_required_models(self):
        self.assertEqual(
            missing_required_models(["gpt-4o", "gpt-4o-mini", "gpt-4o"], ["gpt-4o-mini"]),
            ["gpt-4o"],
        )


if __name__ == "__main__":
    unittest.main()
