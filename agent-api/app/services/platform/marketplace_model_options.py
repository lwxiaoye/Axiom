from typing import Any


def build_marketplace_model_options(chat_models: list[Any], embedding_model_ids: list[Any]) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    seen: set[str] = set()

    for model in chat_models:
        model_id = str(getattr(model, "id", "") or "").strip()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        options.append(
            {
                "label": getattr(model, "name", None) or model_id,
                "value": model_id,
                "available": True,
                "source": "chat",
            }
        )

    for model_id in embedding_model_ids:
        value = str(model_id or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        options.append(
            {
                "label": value,
                "value": value,
                "available": True,
                "source": "embedding",
            }
        )

    return options
