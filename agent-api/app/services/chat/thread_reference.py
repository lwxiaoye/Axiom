"""「最近的对话」引用（composer + 菜单）：把用户显式选中的历史会话渲染成转录附件注入本轮。

走的是「我的文件」那条既有通道（`file_ids` → build_chat_attachments → 合并进
attachments_payload），于是附件已经具备的一切**全部白送**：进模型上下文、落
attachments_json 供刷新/历史回放、读取失败时被 _attachments_degradation_note 捕获后
硬注入模型逼它如实告知。这里只负责「把会话变成一段文本」，不新开注入路径。

三条口径（2026-07-28 定）：

1. **只带正文，不带执行痕迹**。工具调用、思考、子智能体分段一概不进转录——它们体量是
   正文的数倍而复用价值最低。真正常被追问的"上次那个产物"改用文件清单承载（见下）。
2. **裁剪从旧往新裁**，因为「最近说了什么」几乎总比开头更相关。被裁掉的头部若该会话已有
   压缩摘要（context_service 的 compaction 产物）就用摘要顶上，否则明写省略了多少条——
   模型看得见缺口才不会拿半截对话当全貌。
3. **裁剪不算降级**。status 恒为 ok（真正取不到才 failed）：省略是设计而非故障，若标成
   partial，每引用一个长会话都会被 _attachments_degradation_note 逼出一句"我没读全"的
   开场白，噪音远大于价值。缺口写在正文里，模型需要时自己提。
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select

from app.core.database import async_session
from app.models import ChatMessage, ChatThread, AgentUserFile, live_chat_message_clause
from app.services.files.thread_attachment_service import THREAD_REF_KIND
from app.services.platform.token_estimator import estimate_tokens

logger = logging.getLogger(__name__)

# 单轮最多引用几个会话。3 个已能覆盖「把上次那几次讨论合起来看」，再多必然挤掉当轮正题。
MAX_THREADS = 3
# 单个会话的转录预算（估算 token）。CJK 下约等于 6000 字。
PER_THREAD_TOKEN_BUDGET = 6000
# 全部引用会话合计预算：3 × 6000 会吃掉小窗口模型的大半，合计再压一道。
TOTAL_TOKEN_BUDGET = 12000
# 单条消息上限：一条能装下整份报告正文的助手消息不该独吞整个会话预算，超了掐头留尾。
PER_MESSAGE_TOKEN_CAP = 1200
# 产物清单最多列几个文件
MAX_ARTIFACT_FILES = 20

_ROLE_LABEL = {"user": "用户", "assistant": "助手"}


def _truncate_middle(text: str, token_cap: int) -> str:
    """超长单条消息掐头留尾（首尾都保留：开头有意图、结尾有结论）。"""
    if estimate_tokens(text) <= token_cap:
        return text
    # 估算器对 CJK 约 1 字 1 token，对 ASCII 约 4 字 1 token；按最保守的 1:1 折算成字符数，
    # 宁可少留也不要超预算（少留的部分由省略号显式告知）。
    keep = max(200, token_cap)
    head = text[: int(keep * 0.6)]
    tail = text[-int(keep * 0.4):]
    return f"{head}\n……（此条内容过长，中间省略）……\n{tail}"


async def _load_thread(session, user_id: str, thread_id: str) -> Optional[ChatThread]:
    """按归属取会话。跨用户/不存在一律当不存在——不能让引用变成越权读别人对话的口子。"""
    row = await session.get(ChatThread, thread_id)
    if row is None or row.user_id != user_id:
        return None
    return row


async def _load_messages(session, thread_id: str) -> list[ChatMessage]:
    return list(
        (
            await session.execute(
                select(ChatMessage)
                .where(ChatMessage.thread_id == thread_id)
                # live 口径：superseded（重新生成的旧版）与 archived（编辑重发死分支）都不引用，
                # 引用要的是"这个会话最终成立的内容"，不是它的修改史。
                .where(live_chat_message_clause())
                .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
            )
        ).scalars().all()
    )


async def _load_artifacts(session, user_id: str, thread_id: str) -> list[str]:
    """该会话产出的文件名清单。承载「上次那个 PPT」这类追问——名字即统一文件系统里的路径，
    模型可直接 read_file(path=...) / bash 处理，不必把内容塞进转录。"""
    rows = (
        await session.execute(
            select(AgentUserFile.filename)
            .where(
                AgentUserFile.user_id == user_id,
                AgentUserFile.thread_id == thread_id,
                AgentUserFile.source.in_(("generated", "research")),
            )
            .order_by(AgentUserFile.created_at.desc())
            .limit(MAX_ARTIFACT_FILES)
        )
    ).scalars().all()
    return [str(name) for name in rows if name]


def _render_transcript(
    *,
    title: str,
    messages: list[ChatMessage],
    summary_text: str,
    artifacts: list[str],
    budget: int,
) -> tuple[str, int]:
    """渲染转录正文，返回 (文本, 实际用掉的估算 token)。

    从**最新**一条往回收，收满预算为止；被丢下的头部用摘要或省略说明补位。
    """
    kept: list[str] = []
    used = 0
    dropped = 0
    for consumed, msg in enumerate(reversed(messages)):
        role = _ROLE_LABEL.get(str(msg.role or ""), str(msg.role or "未知"))
        body = _truncate_middle(str(msg.content or "").strip(), PER_MESSAGE_TOKEN_CAP)
        if not body:
            continue
        block = f"【{role}】{body}"
        cost = estimate_tokens(block)
        if used + cost > budget and kept:
            # 预算耗尽：剩下的（更早的）全部计为省略。注意 `and kept` —— 哪怕第一条就超预算
            # 也要留下它，否则会得到一份空转录，用户看着"引用了"其实什么都没带。
            # dropped 按**未走到的下标**算，不能用 len(messages)-len(kept)——空正文的消息被
            # continue 跳过却没进 kept，那样算会把它们重复计成"被省略"。
            dropped = len(messages) - consumed
            break
        kept.append(block)
        used += cost
    kept.reverse()

    head = f"用户引用了历史会话《{title}》。以下是该会话的对话记录，本轮请结合它作答。"
    parts = [head]
    if dropped > 0:
        if summary_text:
            parts.append(
                f"--- 更早内容（共 {dropped} 条）的摘要 ---\n{_truncate_middle(summary_text, 800)}"
            )
        else:
            parts.append(
                f"（注意：该会话更早的 {dropped} 条消息因篇幅未包含在内。"
                "如果用户问到的内容不在下面的记录里，请如实说明你只看到了这次会话的后半段，不要编造。）"
            )
    parts.append(("--- 对话记录 ---\n" + "\n\n".join(kept)) if kept else "（该会话暂无消息）")
    if artifacts:
        parts.append(
            "--- 该会话产出的文件 ---\n"
            + "、".join(artifacts)
            + f"\n（共 {len(artifacts)} 个，都在用户的文件工作区里，可用 read_file(path='文件名') 读取，"
            "或用 bash 在沙箱 /workspace/files/ 下处理。）"
        )
    text = "\n\n".join(parts)
    return text, estimate_tokens(text)


def _attachment_filename(title: str) -> str:
    """附件名（也是 UI 上的卡片标题）。

    结尾必须缀上「（对话记录）」，不只是为了好看——tool_scope._detect_kinds 用
    `Path(name).suffix` 判产物类型，一个标题叫「帮我改 report.xlsx」的会话若直接当文件名，
    会被误判成本轮在处理 Excel，凭空放出 apply_excel_patch 之类的办公工具。缀一段不含点号
    的后缀，suffix 就落在映射表外，误触自然消失。
    """
    clean = (title or "未命名对话").strip() or "未命名对话"
    return f"{clean[:60]}（对话记录）"


async def build_thread_attachments(
    user_id: str,
    thread_ids: list,
    *,
    current_thread_id: Optional[str] = None,
) -> list[dict]:
    """把 composer 选中的历史会话解析为附件块（与 build_chat_attachments 同形态）。

    - 归属校验：非本人会话按「不存在」处理，降级为 failed 占位块而不是整轮 404；
    - 自引用过滤：选中的就是当前会话时静默跳过（内容本来就在上下文里，重复注入纯浪费）；
    - 超过 MAX_THREADS 的部分不静默丢弃，追加 failed 占位块，逼模型如实告知。
    """
    raw_ids: list[str] = []
    for tid in thread_ids or []:
        s = str(tid or "").strip()
        # 去重：同一个会话选两次没有意义，注入两遍反而挤预算
        if s and s not in raw_ids:
            raw_ids.append(s)

    current = str(current_thread_id or "").strip()
    ids = [tid for tid in raw_ids if tid != current]

    kept_ids, dropped_ids = ids[:MAX_THREADS], ids[MAX_THREADS:]
    out: list[dict] = []
    remaining = TOTAL_TOKEN_BUDGET

    for tid in kept_ids:
        try:
            async with async_session() as session:
                thread = await _load_thread(session, user_id, tid)
                if thread is None:
                    out.append({
                        "filename": "已失效的对话",
                        "kind": THREAD_REF_KIND,
                        "image_url": "",
                        "file_id": "",
                        "text": f"（用户选中的历史会话不可用：会话 {tid} 不存在或无权访问。）",
                        "status": "failed",
                        "note": "会话不存在或无权访问",
                    })
                    continue
                messages = await _load_messages(session, tid)
                artifacts = await _load_artifacts(session, user_id, tid)
        except Exception as e:  # noqa: BLE001
            logger.warning("引用历史会话失败 %s: %s", tid, e)
            out.append({
                "filename": "读取失败的对话",
                "kind": THREAD_REF_KIND,
                "image_url": "",
                "file_id": "",
                "text": f"（用户选中的历史会话读取失败：{e}）",
                "status": "failed",
                "note": "会话读取失败",
            })
            continue

        # 摘要走 Runtime PG（未配置时为 None），失败按无摘要处理——它只是裁剪时的补位，
        # 拿不到不该拖垮整个引用。
        summary_text = ""
        try:
            from app.services.memory import context_service

            summary = await context_service.get_summary(tid)
            if summary:
                summary_text = str(summary.get("summary") or "").strip()
        except Exception as e:  # noqa: BLE001
            logger.debug("引用历史会话取摘要失败（按无摘要处理）%s: %s", tid, e)

        budget = min(PER_THREAD_TOKEN_BUDGET, max(0, remaining))
        if budget <= 0:
            out.append({
                "filename": _attachment_filename(thread.title or ""),
                "kind": THREAD_REF_KIND,
                "image_url": "",
                "file_id": "",
                "text": (
                    f"（用户选中了历史会话《{thread.title or '未命名对话'}》，但本轮引用的会话内容已达上下文上限，"
                    "这一个没有被读取。如需查看请让用户单独引用它，不要假装读过。）"
                ),
                "status": "failed",
                "note": "引用内容超出上下文预算，未读取",
            })
            continue

        text, used = _render_transcript(
            title=thread.title or "未命名对话",
            messages=messages,
            summary_text=summary_text,
            artifacts=artifacts,
            budget=budget,
        )
        remaining -= used
        out.append({
            "filename": _attachment_filename(thread.title or ""),
            "kind": THREAD_REF_KIND,
            "image_url": "",
            # file_id 留空：它不是「我的文件」里的文件，没有可操作的 id。别塞会话 id 冒充，
            # 模型会拿它去调 read_file/call_subagent(file_ids=...) 然后软失败。
            "file_id": "",
            "text": text,
            "status": "ok",
            "note": None,
        })

    if dropped_ids:
        logger.info(
            "build_thread_attachments 超过单轮 %d 个会话上限，丢弃 %d 个: %s",
            MAX_THREADS, len(dropped_ids), dropped_ids,
        )
        out.append({
            "filename": f"另有 {len(dropped_ids)} 个对话未读取",
            "kind": THREAD_REF_KIND,
            "image_url": "",
            "file_id": "",
            "text": (
                f"（用户本轮引用的历史会话超过单轮最多 {MAX_THREADS} 个的上限，"
                f"其中 {len(dropped_ids)} 个未被读取；如需查看请让用户分批引用，"
                "不要当作已看过全部引用的会话。）"
            ),
            "status": "failed",
            "note": f"超过单轮 {MAX_THREADS} 个会话上限，另有 {len(dropped_ids)} 个未读取",
        })

    return out
