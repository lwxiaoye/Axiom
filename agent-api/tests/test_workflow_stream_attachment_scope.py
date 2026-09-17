import unittest

from app.routers.workflow import DebugRequest, _workflow_stream_variables


class WorkflowStreamAttachmentScopeTest(unittest.TestCase):
    def test_text_only_turn_does_not_replay_prior_attachment_as_current(self):
        payload = DebugRequest(
            input="住宿费一年多少钱？",
            variables={},
            histories=[{"role": "assistant", "content": "你上传的图片是黑色横条。"}],
        )

        variables = _workflow_stream_variables(payload, ["historical-image"])

        self.assertEqual(variables["userFileIds"], [])
        self.assertEqual(variables["currentTurnUserFileIds"], [])
        self.assertEqual(variables["historicalUserFileIds"], ["historical-image"])
        self.assertEqual(variables["histories"], payload.histories)

    def test_current_and_historical_attachment_channels_remain_separate(self):
        payload = DebugRequest(
            input="请解释这张图",
            variables={"userFileIds": ["current-image", "current-image"]},
        )

        variables = _workflow_stream_variables(
            payload,
            ["historical-image", "current-image"],
        )

        self.assertEqual(variables["userFileIds"], ["current-image"])
        self.assertEqual(variables["currentTurnUserFileIds"], ["current-image"])
        self.assertEqual(variables["historicalUserFileIds"], ["historical-image"])


if __name__ == "__main__":
    unittest.main()
