import unittest

from app.services.platform.marketplace_model_options import build_marketplace_model_options


class MarketplaceModelOptionsTest(unittest.TestCase):
    def test_builds_chat_and_embedding_options_with_deduped_values(self):
        chat_model = type("Model", (), {"id": "gpt-4o", "name": "GPT-4o"})()

        self.assertEqual(
            build_marketplace_model_options([chat_model], ["text-embedding-v3", "gpt-4o", ""]),
            [
                {"label": "GPT-4o", "value": "gpt-4o", "available": True, "source": "chat"},
                {
                    "label": "text-embedding-v3",
                    "value": "text-embedding-v3",
                    "available": True,
                    "source": "embedding",
                },
            ],
        )


if __name__ == "__main__":
    unittest.main()
