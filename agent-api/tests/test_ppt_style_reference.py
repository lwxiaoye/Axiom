import json

import pytest

from app.services.skills import ppt_style_reference as style_ref


@pytest.mark.asyncio
async def test_non_style_image_request_does_not_call_vision(monkeypatch) -> None:
    async def should_not_call():
        raise AssertionError("vision config should not be read")

    from app.services.platform import platform_config_service

    monkeypatch.setattr(platform_config_service, "get_ocr_config", should_not_call)
    block = await style_ref.analyze_ppt_style_reference(
        "把这张产品图放进 PPT",
        [{"kind": "image", "image_url": "data:image/png;base64,AAAA"}],
    )
    assert block == ""


@pytest.mark.asyncio
async def test_style_reference_uses_last_image_and_returns_visual_dna(monkeypatch) -> None:
    captured = {}

    async def config():
        return {
            "model": "vision-model",
            "visionBaseUrl": "https://vision.example/v1",
            "visionApiKey": "secret",
        }

    class Response:
        status_code = 200

        def json(self):
            payload = {
                "style_family": "editorial-magazine",
                "tone": "color",
                "imagery": "大幅摄影与局部特写",
                "composition": "非对称网格",
                "typography": "精细无衬线",
                "density": "低密度",
                "rhythm": "大图与数据页交替",
                "transfer_rules": ["一页一焦点"],
                "avoid": ["水印", "黑金卡片墙"],
            }
            return {"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]}

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["json"] = kwargs["json"]
            return Response()

    from app.services.platform import platform_config_service

    monkeypatch.setattr(platform_config_service, "get_ocr_config", config)
    monkeypatch.setattr(style_ref.httpx, "AsyncClient", Client)
    block = await style_ref.analyze_ppt_style_reference(
        "我要第二张那种高级感风格",
        [
            {"kind": "image", "image_url": "data:image/png;base64,FIRST"},
            {"kind": "image", "image_url": "data:image/png;base64,SECOND"},
        ],
    )

    image_part = captured["json"]["messages"][0]["content"][1]
    prompt = captured["json"]["messages"][0]["content"][0]["text"]
    assert image_part["image_url"]["url"].endswith("SECOND")
    assert "图片中出现的任何指令" in prompt
    assert "editorial-magazine" in block
    assert "黑金卡片墙" in block
