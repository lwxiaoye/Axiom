"""模型上下文窗口解析（§13）。

自动压缩/预算需要知道「当前模型的真实窗口」，不能写死 32000。解析顺序（2026-07-27
借鉴 pi 的 models.dev 管线重构，此前手工表把 deepseek-v4/qwen3.7 低估了 15 倍）：
① 运维覆盖（渠道真实上限只有运维知道）→ ② models.dev 生成表精确命中（开发机跑
scripts/sync_model_windows.py 更新，内网运行期不外拉）→ ③ 手工子串表（网关别名 +
离线兜底，不再是事实源）→ ④ 保守默认 `CONTEXT_WINDOW_DEFAULT`。
"""
import json
import logging
from functools import lru_cache
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)

_GENERATED_PATH = Path(__file__).resolve().parents[2] / "data" / "model_windows_generated.json"


@lru_cache(maxsize=1)
def _generated_windows() -> dict:
    """models.dev 生成表（精确 id → 窗口）。文件缺失/损坏降级为空表（走手工表兜底）。"""
    try:
        payload = json.loads(_GENERATED_PATH.read_text())
        return {str(k).lower(): int(v) for k, v in (payload.get("windows") or {}).items()}
    except Exception as exc:  # noqa: BLE001
        logger.warning("model_windows_generated.json 不可用，退回手工子串表: %s", exc)
        return {}

# 子串关键字 → 窗口（token）。顺序：更具体的关键字放前面（先命中）。
_WINDOWS = [
    ("gpt-4.1", 1_000_000),
    ("gpt-4o", 128_000),
    ("gpt-4-turbo", 128_000),
    ("gpt-4-32k", 32_768),
    ("gpt-4", 8_192),
    ("gpt-3.5-16k", 16_385),
    ("gpt-3.5", 16_385),
    ("o1", 200_000),
    ("o3", 200_000),
    ("o4", 200_000),
    ("claude-3", 200_000),
    ("claude", 200_000),
    ("gemini-2", 1_000_000),
    ("gemini-1.5", 1_000_000),
    ("gemini", 1_000_000),
    # deepseek v4 代际 1M（2026-07-27 用户实测口径纠正：旧表把 v4-flash 按 64K 压在 38K
    # 就触发压缩，1M 窗口只用到 4%）。更具体的 key 必须放在通配 "deepseek" 之前。
    ("deepseek-v4", 1_000_000),
    ("deepseek", 64_000),
    ("qwen-long", 1_000_000),
    ("qwen-turbo", 1_000_000),
    ("qwen-flash", 1_000_000),
    ("qwen-plus", 131_072),
    ("qwen3", 131_072),
    ("qwen2.5", 128_000),
    ("qwen2", 32_768),
    ("qwen", 32_768),  # 兜底：qwen-max/更旧型号真实窗口约 32K
    ("glm-4.6", 200_000),
    ("glm-4.5", 128_000),
    ("glm-4", 128_000),
    ("glm", 128_000),
    ("moonshot", 128_000),
    ("kimi", 128_000),
    ("doubao", 128_000),
    ("ernie", 8_192),
    ("baichuan", 32_768),
    ("yi-", 32_768),
    ("abab", 245_000),
    ("minimax", 245_000),
    ("spark", 8_192),
    ("hunyuan", 32_768),
]


def resolve_window(model: str) -> int:
    """按模型 id 子串匹配返回 context window（token）；未命中用默认。

    运维覆盖（settings.MODEL_WINDOW_OVERRIDES）优先于内置表——网关渠道的真实上限只有运维知道，
    内置表按模型原生窗口给保守值。覆盖同样按子串匹配，更长的 key 先命中（更具体优先）。
    """
    m = (model or "").strip().lower()
    if not m:
        return settings.CONTEXT_WINDOW_DEFAULT
    overrides = settings.MODEL_WINDOW_OVERRIDES or {}
    for key in sorted(overrides, key=len, reverse=True):
        if key and key.lower() in m:
            return int(overrides[key])
    # models.dev 生成表精确命中（网关 id 常带命名空间/别名前缀，也按末段再试一次）
    generated = _generated_windows()
    hit = generated.get(m) or generated.get(m.rsplit("/", 1)[-1])
    if hit:
        return int(hit)
    for key, win in _WINDOWS:
        if key in m:
            return win
    return settings.CONTEXT_WINDOW_DEFAULT


def history_budget(window: int) -> int:
    """本轮「历史 + 摘要」占窗口的目标上限（预留系统提示与输出空间）。"""
    return max(1024, int(window * settings.CONTEXT_BUDGET_RATIO) - settings.CONTEXT_RESERVE_OUTPUT_TOKENS)


def compact_trigger(window: int) -> int:
    """压缩触发线（2026-07-27 pi 对齐：`窗口 − 预留`，封顶 92% 利用率）。

    旧公式 0.6×窗口对 1M 模型意味着 60 万 token 就开始丢细节；pi 用真实 usage 敢压到
    窗口−16K（~92%）。我们的用量信号=估算×校准因子 + 线程级真实 usage 下限锚，残差比 pi
    大一点，故再封顶 92%。预留随窗口自适应：小窗口取 10%（16K 预留会吃掉 32K 模型半个窗）。
    """
    reserve = max(4096, min(settings.CONTEXT_COMPACT_RESERVE_TOKENS, int(window * 0.10)))
    return max(2048, min(window - reserve, int(window * settings.CONTEXT_COMPACT_MAX_UTILIZATION)))
