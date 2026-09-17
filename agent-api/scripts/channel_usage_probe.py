"""渠道 usage 语义探针：新渠道/新模型接入清单必跑的一条。

用法: docker exec agent-api python -u scripts/channel_usage_probe.py [model ...]
（不带参数=探网关上该 key 可见的全部 chat 模型；每个模型一条 ~20 token 的请求）

背景（2026-07-30 事故）：OpenAI 语义下流式 usage 只随末帧出现一次，但 step 系渠道
**每帧**都带「累计到当前」的 usage。工具循环若按逐帧 += 计量，累计值会被反复相加
（真机一轮被记成 31.9 万 token），token 熔断误触发强制收敛，表象是「模型调不了
工具」。计量已改为帧内覆盖+轮末并入（对两种语义都正确），但**新渠道接入时仍应
先跑本探针**：若出现第三种语义（每帧增量）或「无usage!」（熔断维度失明），要先
补适配再上线。

输出每模型一行：帧数 / usage 出现在几帧 / 若为累计式则给出旧算法会虚记成多少。

基线快照（2026-07-30 实测 29 模型——重跑时对着它答「这是不是新情况」）：
每帧累计式仅 step-3.7-flash（12/13 帧带 usage）；deepseek-v4-flash/pro、
glm-4-flash、glm-4.6v、glm-4.7、qwen 系 7 个可达型号均「仅末帧」；
未见「每帧增量」第三种语义。当时不可达（渠道故障非语义结论）：glm 其余
429 余额不足、qwen3.5-plus 与两个 qwen3.7-max 快照 403 quota exhausted、
glm-4.7-flash 429 限流。
"""
import asyncio
import json
import sys

import httpx

sys.path.insert(0, "/app")

BASE = "http://127.0.0.1:9080/new-api/v1"
SKIP_SUBSTR = ("embedding",)  # 无 chat 口的模型族


async def probe(key: str, model: str, sem: asyncio.Semaphore) -> str:
    async with sem:
        payload = {"model": model, "stream": True, "max_tokens": 20,
                   "stream_options": {"include_usage": True},
                   "messages": [{"role": "user", "content": "回复一个字：好"}]}
        n = nu = last = summed = 0
        try:
            async with httpx.AsyncClient(timeout=25) as c:
                async with c.stream("POST", BASE + "/chat/completions", json=payload,
                                    headers={"Authorization": f"Bearer {key}"}) as r:
                    if r.status_code >= 400:
                        body = (await r.aread()).decode("utf-8", "ignore")
                        return f"{model:28s} HTTP {r.status_code} {body[:70]}"
                    async for line in r.aiter_lines():
                        line = (line or "").strip()
                        if not line.startswith("data:"):
                            continue
                        d = line[5:].strip()
                        if d == "[DONE]":
                            break
                        try:
                            ch = json.loads(d)
                        except json.JSONDecodeError:
                            continue
                        n += 1
                        ct = (ch.get("usage") or {}).get("completion_tokens")
                        if ct:
                            nu += 1
                            last = int(ct)
                            summed += int(ct)
        except Exception as e:  # noqa: BLE001 - 探针容错，逐模型上报
            return f"{model:28s} ERR {type(e).__name__} {str(e)[:55]}"
        if nu == 0:
            kind = "无usage!（token 熔断维度失明，接入前先查渠道配置）"
        elif nu <= 1:
            kind = "仅末帧 ✓"
        else:
            kind = f"每帧累计×{nu} ⚠  逐帧+=会记{summed}(真实{last})"
        return f"{model:28s} 帧{n:3d}  {kind}"


async def main() -> int:
    from app.services.platform.key_service import key_service
    key = await key_service.get_user_key("e2e-progress-check")
    if not key:
        print("SKIP: E2E 账号无可用 key")
        return 2
    models = sys.argv[1:]
    if not models:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(BASE + "/models",
                            headers={"Authorization": f"Bearer {key}"})
            models = sorted(m["id"] for m in r.json().get("data", [])
                            if not any(s in m["id"] for s in SKIP_SUBSTR))
    print(f"探测 {len(models)} 个模型（每帧累计⚠ 与 无usage! 都要在接入前处理）")
    sem = asyncio.Semaphore(5)
    for line in await asyncio.gather(*(probe(key, m, sem) for m in models)):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
