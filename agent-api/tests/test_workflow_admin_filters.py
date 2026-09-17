import unittest

from app.routers.workflow import normalize_ai_app_types


class WorkflowAdminFilterTest(unittest.TestCase):
    def test_normalize_ai_app_types_accepts_single_and_csv_values(self):
        self.assertEqual(normalize_ai_app_types("chatAgent", None), ["chatAgent"])
        self.assertEqual(normalize_ai_app_types(None, "chatAgent, workflow,,chatAgent"), ["chatAgent", "workflow"])

    def test_normalize_ai_app_types_prefers_explicit_single_filter(self):
        self.assertEqual(normalize_ai_app_types("workflow", "chatAgent,workflow"), ["workflow"])


if __name__ == "__main__":
    unittest.main()
