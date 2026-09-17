#!/usr/bin/env python3
"""从 models.dev 同步模型上下文窗口表（借鉴 pi 的 generate-models 管线，2026-07-27）。

pi 的做法是四层：models.dev 构建期生成静态表 → 发布 CDN → 运行期增量刷新 → 用户覆盖。
我们服务器在内网、网关模型只有几十个，取第 1、4 层就够：本脚本在**开发机**上运行
（需要外网），把 https://models.dev/api.json 归一化成 {模型id: context_window} 写进
app/data/model_windows_generated.json 并提交进仓库；运行期 model_window.resolve_window
以「运维覆盖 > 生成表精确命中 > 手工子串表 > 保守默认」的顺序解析。

手工子串表从此只承担两个角色：生成表没有的网关别名，以及离线兜底——不再是事实源。
模型代际更新时重跑本脚本即可（例：deepseek-v4/qwen3.7 曾被旧手工表低估 15 倍）。

用法：python3 scripts/sync_model_windows.py [--input 本地api.json] [--check]
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

API_URL = "https://models.dev/api.json"
OUT_PATH = Path(__file__).resolve().parent.parent / "app" / "data" / "model_windows_generated.json"

# 「原厂」provider：同一模型多家转售报的窗口不一致时，原厂口径优先（转售常缩水或虚标）。
_CANONICAL_PROVIDERS = {
    "deepseek", "zhipuai", "alibaba", "alibaba-cn", "moonshotai", "moonshotai-cn",
    "openai", "anthropic", "google", "xai", "mistral", "minimax", "minimax-cn",
    "baidu", "volcengine", "stepfun", "tencent", "iflytek", "01-ai",
}


def _normalize(model_id: str) -> str:
    """网关/聚合商常带命名空间前缀（deepseek/deepseek-v4-flash、deepseek-ai/DeepSeek-V4-Flash），
    取末段小写作为匹配键，与我们 new-api 暴露的裸模型 id 对齐。"""
    return model_id.rsplit("/", 1)[-1].strip().lower()


def build_windows(api: dict) -> dict[str, int]:
    # id → [(provider, context), ...]
    seen: dict[str, list[tuple[str, int]]] = {}
    for provider_id, provider in api.items():
        for model_id, model in (provider.get("models") or {}).items():
            ctx = ((model.get("limit") or {}).get("context"))
            if not isinstance(ctx, int) or ctx <= 0:
                continue
            seen.setdefault(_normalize(model_id), []).append((provider_id, ctx))

    windows: dict[str, int] = {}
    for mid, entries in seen.items():
        canonical = [ctx for pid, ctx in entries if pid in _CANONICAL_PROVIDERS]
        if canonical:
            # 原厂多条（同厂不同 region 等）取最大＝原生窗口
            windows[mid] = max(canonical)
        else:
            # 无原厂口径：取众数（多数转售商一致的值），并列取更小的（估小只是早压缩，估大会撞墙）
            counts = Counter(ctx for _, ctx in entries)
            top = max(counts.values())
            windows[mid] = min(ctx for ctx, n in counts.items() if n == top)
    return dict(sorted(windows.items()))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", help="本地 api.json（默认拉取 models.dev）")
    parser.add_argument("--check", action="store_true", help="只对比不写入（CI 陈旧检查）")
    args = parser.parse_args()

    if args.input:
        api = json.loads(Path(args.input).read_text())
    else:
        with urllib.request.urlopen(API_URL, timeout=60) as resp:  # noqa: S310
            api = json.load(resp)

    windows = build_windows(api)
    payload = {
        "_source": API_URL,
        "_generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "_model_count": len(windows),
        "windows": windows,
    }
    if args.check:
        old = json.loads(OUT_PATH.read_text())["windows"] if OUT_PATH.exists() else {}
        changed = {k: (old.get(k), v) for k, v in windows.items() if old.get(k) != v}
        print(f"{len(changed)} 条差异" + (f"，示例：{list(changed.items())[:5]}" if changed else ""))
        return 1 if changed else 0

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n")
    print(f"已写入 {OUT_PATH}（{len(windows)} 个模型）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
