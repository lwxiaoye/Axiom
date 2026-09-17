"""会话附件资产（任务模式设计稿 §5，2026-07-16 重启持久化）。

**核心规则**：附件随消息**发送**后成为会话资产——此后任何一轮主对话都恒可读（判定点是「发送」
不是「上传」）。前端 composer 里已上传未发送的附件是草稿，撤掉不留痕；随消息发出后 persist 落库
并绑定该消息，全会话生命周期可读，删消息/删会话时级联清理。

每轮用当轮问题对**全部会话附件**（历史发过的 + 本轮新发的，按 sha256 去重）逐个做内存向量重检索
（session_file_service，embedding 按内容缓存），只注入相关片段；片段只进模型输入不进落库消息 content。
附件与「我的文件」共用同一个 file_id。解析文本用于检索，原始字节由用户文件服务保存；
需要修改时必须按 file_id 原位写回并产生版本，不能按文件名寻找另一份文件代替。
"""
import hashlib
import logging
from typing import Any, List, Optional

from sqlalchemy import delete, select

from app.core.runtime_db import runtime_session
from app.services.files import session_file_service

logger = logging.getLogger(__name__)

# 全部附件注入的总字符预算（单文件预算见 session_file_service）
TOTAL_CONTEXT_BUDGET = 12000

# 「最近的对话」引用块（chat/thread_reference.py 产出）的 kind 标记。
# 它借 attachments 通道只是为了白拿元数据落库与历史回放，**语义与上传文件完全相反**，
# 本模块两处必须把它排除在外：
# - persist_for_message 不持久化它：引用是一次性的，一旦落成会话资产，这份几千 token 的
#   转录会在此后**每一轮**被重新注入，与「引用一次」的产品语义直接冲突；
# - build_file_context 原文注入、不过 retrieve_relevant：转录在生成时已按 token 预算裁剪、
#   保留了首尾与「更早 N 条已省略」的说明，再按相关度切一遍只会把它切碎，
#   还会让那句省略说明连同上下文一起消失（模型于是把半截对话当全貌）。
THREAD_REF_KIND = "thread_ref"


def _sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="ignore")).hexdigest()


def _normalize(attachments: Optional[List[Any]]) -> List[dict]:
    result = []
    for att in attachments or []:
        def g(k):
            return att.get(k) if isinstance(att, dict) else getattr(att, k, "")
        name = g("filename")
        text = g("text")
        file_id = str(g("file_id") or "").strip()
        if text or file_id:
            parsed_text = str(text or "")
            result.append({
                "filename": str(name or "文件")[:255],
                "text": parsed_text,
                "kind": str(g("kind") or ""),
                "file_id": file_id,
                # 新上传使用原始字节哈希；旧客户端/历史数据退化为解析文本哈希。
                "sha256": str(g("sha256") or "").strip() or _sha(parsed_text),
            })
    return result


async def persist_for_message(
    *, thread_id: str, user_id: str, message_id: Optional[int],
    attachments: Optional[List[Any]],
) -> None:
    """用户消息发送落库后调用：把本轮随消息发出的附件持久化为会话资产（§5 判定点=发送）。
    sha256 去重——同一 thread 内已存在相同内容则跳过（重复发送不堆重份）。尽力而为，失败不阻断对话。

    「最近的对话」引用块（THREAD_REF_KIND）不落会话资产——见该常量的说明。"""
    files = [
        f for f in _normalize(attachments)
        if f.get("kind") not in {THREAD_REF_KIND, "skill", "knowledge", "subagent", "web"}
    ]
    if not files:
        return
    factory = runtime_session()
    if factory is None:
        return  # Runtime 域库未配置：退化为纯当轮注入（下方 build_file_context 仍用本轮 attachments）
    import uuid
    from app.runtime_models import AgentThreadAttachment
    try:
        async with factory() as session:
            existing = set((
                await session.execute(
                    select(AgentThreadAttachment.sha256).where(
                        AgentThreadAttachment.thread_id == thread_id
                    )
                )
            ).scalars().all())
            for att in files:
                digest = att["sha256"]
                if digest in existing:
                    continue
                existing.add(digest)
                session.add(AgentThreadAttachment(
                    id=uuid.uuid4().hex, thread_id=thread_id, user_id=user_id,
                    message_id=message_id, kind=att["kind"], filename=att["filename"],
                    file_id=att["file_id"] or None,
                    sha256=digest, text=att["text"], char_len=len(att["text"]),
                ))
            await session.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("持久化会话附件失败（不阻断对话）: %s", e)


async def _load_thread_attachments(thread_id: str, user_id: str) -> List[dict]:
    """加载本会话已发送的全部附件资产（时间升序）。库未配置/异常 → 空（退化当轮制）。"""
    factory = runtime_session()
    if factory is None:
        return []
    from app.runtime_models import AgentThreadAttachment
    try:
        async with factory() as session:
            rows = (
                await session.execute(
                    select(AgentThreadAttachment)
                    .where(AgentThreadAttachment.thread_id == thread_id,
                           AgentThreadAttachment.user_id == user_id)
                    .order_by(AgentThreadAttachment.created_at.asc())
                )
            ).scalars().all()
        return [{
            "filename": r.filename,
            "text": r.text,
            "sha256": r.sha256,
            "file_id": str(getattr(r, "file_id", "") or ""),
        } for r in rows]
    except Exception as e:  # noqa: BLE001
        logger.warning("加载会话附件失败（退化当轮制）: %s", e)
        return []


async def delete_for_thread(thread_id: str) -> None:
    """Thread 删除时清理其全部会话附件（尽力而为，失败不阻断删除）。"""
    factory = runtime_session()
    if factory is None:
        return
    from app.runtime_models import AgentThreadAttachment
    try:
        async with factory() as session:
            await session.execute(
                delete(AgentThreadAttachment).where(AgentThreadAttachment.thread_id == thread_id)
            )
            await session.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("清理会话附件失败: %s", e)


async def delete_for_messages(thread_id: str, message_ids: List[int]) -> None:
    """删除/截断消息时级联清理其绑定的会话附件（§5：删消息即清理该附件）。"""
    if not message_ids:
        return
    factory = runtime_session()
    if factory is None:
        return
    from app.runtime_models import AgentThreadAttachment
    try:
        async with factory() as session:
            await session.execute(
                delete(AgentThreadAttachment).where(
                    AgentThreadAttachment.thread_id == thread_id,
                    AgentThreadAttachment.message_id.in_(message_ids),
                )
            )
            await session.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("级联清理消息附件失败: %s", e)


async def recent_file_targets(
    *, thread_id: str, user_id: str, limit: int = 3,
) -> List[dict]:
    """返回本会话最近发送过的精确文件目标。

    供“把这份文件换成蓝色”这类后续轮指代解析使用。只返回带 file_id 的会话附件，
    不按文件名回查「我的文件」，最终写权限仍由 user_file_service 的归属校验确认。
    """
    factory = runtime_session()
    if factory is None:
        return []
    from app.runtime_models import AgentThreadAttachment
    try:
        async with factory() as session:
            rows = (
                await session.execute(
                    select(AgentThreadAttachment)
                    .where(
                        AgentThreadAttachment.thread_id == thread_id,
                        AgentThreadAttachment.user_id == user_id,
                        AgentThreadAttachment.file_id.isnot(None),
                    )
                    .order_by(AgentThreadAttachment.created_at.desc())
                    .limit(max(1, int(limit)))
                )
            ).scalars().all()
        from app.services.files import user_file_service
        available = await user_file_service.existing_file_ids(
            user_id, [str(row.file_id or "") for row in rows],
        )
        return [{
            "file_id": str(row.file_id or ""),
            "filename": str(row.filename or "文件"),
            "source": "thread_attachment",
            "created_at": row.created_at.isoformat() if row.created_at else None,
        } for row in rows if row.file_id and str(row.file_id) in available]
    except Exception as exc:  # noqa: BLE001
        logger.warning("加载会话最近文件目标失败（不阻断对话）: %s", exc)
        return []


async def _build_generated_files_block(thread_id: str, user_id: str) -> str:
    """本会话助手已生成产物的「可原地修改」上下文块（2026-07-20）。

    背景：用户生成一份文件后说「换个色调 / 改一下 X / 在此基础上…」时，模型此前拿不到那份
    产物的 file_id 与确切文件名，只能凭记忆从头重生成一份（还常另起文件名 → 变成两份）。
    这里把本会话生成物列清单交给模型，并明确「修改即原地改、直接做、别问、别复述内部机制」，
    让「在原文件上改」成为默认且顺滑的行为。修改的落地机制统一文件系统已具备（产物就在沙箱
    /workspace/files/ 下、同名写回即原地覆盖、保持 file_id、版本历史留档），这里只补
    「模型知道文件 + 知道该怎么做」。
    """
    from app.services.files import user_file_service
    try:
        files = await user_file_service.list_generated_files(user_id, thread_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("列本会话生成物失败（不阻断对话）: %s", e)
        return ""
    if not files:
        return ""
    lines = [
        "- 《{name}》 file_id={fid}（约 {kb} KB）".format(
            name=f.get("filename") or "未命名",
            fid=f.get("id") or "",
            kb=max(1, int((f.get("size") or 0) / 1024)),
        )
        for f in files
    ]
    return (
        "（本会话中你已生成并保存到用户「我的文件」的产物如下。当用户要求「修改/换色调/换成…/"
        "调整/改一下/在此基础上…」等**针对已有产物的改动**时，默认在**原文件上修改、不要新建一份**：\n"
        "① 产物就在沙箱 **/workspace/files/<原文件名>**（与「我的文件」是同一批文件）。用 bash "
        "读它、只改用户要求的部分、其余版式内容原样保留，再写回**完全相同的文件名**"
        "——系统会原地覆盖同一文件（file_id 不变、旧版进版本历史），用户看到的还是同一份文件被更新。"
        "纯文本产物也可以直接用 edit_file(path=...) 精确替换。\n"
        "② **直接动手做完**，不要为此反问用户「需要我重新生成吗」之类；也**不要向用户复述内部机制**"
        "（如 SVG/PPTX 管线、二进制限制、系统要求等），这些是实现细节，用户只关心「我的文件被按要求改好了」。\n"
        "③ 收尾就说「已把《X》的 ×× 改为 ××（其余不变）」，不要说成「重新生成了一份」。"
        "只有用户明确说「重新做一份/另存/换一个新的」时才新建文件。）\n"
        + "\n".join(lines)
    )


def _join_blocks(*blocks: str) -> str:
    return "\n\n".join(b for b in blocks if b)


def _build_thread_ref_block(refs: List[dict]) -> str:
    """「最近的对话」引用块：**原文拼接**，不过 retrieve_relevant、不参与附件预算。

    不做相关度检索是刻意的——转录在 chat/thread_reference.py 里已经按 token 预算裁过，
    并在文首写明了"更早的 N 条已省略"。再按当轮问题切一遍，最先被切掉的恰恰是那句省略
    说明和对话的开头，模型于是把半截当全貌。裁剪的判断留在生成侧一处，这里只负责搬运。
    """
    if not refs:
        return ""
    body = "\n\n".join(r["text"] for r in refs if r.get("text"))
    if not body:
        return ""
    return (
        "（以下是用户在本轮**显式引用**的历史会话记录，只对这一轮有效，不是本会话的内容。"
        "引用它来理解用户在说什么、延续之前的结论；但不要把它当成用户刚刚说的话，"
        "也不要在无关的问题上主动复述它。）\n\n" + body
    )


async def build_file_context(
    *,
    thread_id: str,
    user_id: str,
    query: str,
    attachments: Optional[List[Any]] = None,
    audit_context: Optional[dict] = None,
) -> str:
    """构建会话文件上下文块：**用户上传的会话附件**（向量重检索注入相关片段）+ **助手本会话
    已生成的产物清单**（告知模型可原地修改）+ **本轮引用的历史会话转录**（原文注入）。
    三者皆无返回 ""。

    调用方约定：返回值只拼进模型输入（user_input / 最后一条 HumanMessage），
    不得写进落库的用户消息 content。
    """
    generated_block = await _build_generated_files_block(thread_id, user_id)
    normalized = _normalize(attachments)
    # 「最近的对话」引用先摘出来（见 THREAD_REF_KIND）：它只属于本轮，且要原文注入
    turn_files = [
        a for a in normalized
        if a.get("kind") not in {THREAD_REF_KIND, "skill", "knowledge", "subagent", "web"}
    ]
    ref_block = _build_thread_ref_block([a for a in normalized if a.get("kind") == THREAD_REF_KIND])
    persisted = await _load_thread_attachments(thread_id, user_id)
    # 去重合并：已落库的会话资产 + 本轮新发（可能尚未落库，如发送即读的同一请求）
    merged: List[dict] = []
    seen = set()
    for att in persisted + turn_files:
        digest = att.get("sha256") or _sha(att["text"])
        if digest in seen:
            continue
        seen.add(digest)
        merged.append(att)
    if not merged:
        # 无用户上传附件，但可能有本会话生成物（可原地修改）/ 本轮引用的历史会话——
        # 三个块各自独立成立，不能因为没有上传附件就把另外两个一并吞掉
        return _join_blocks(ref_block, generated_block)

    blocks: List[str] = []
    used = 0
    skipped = 0
    for att in merged:
        # Keep the pre-audit callable shape for callers/tests that replace the
        # retriever with a two-argument adapter.  Real audited turns pass a
        # non-empty context and still take the new accounting path.
        if audit_context is None:
            relevant = await session_file_service.retrieve_relevant(
                query or "",
                att["text"],
            )
        else:
            relevant = await session_file_service.retrieve_relevant(
                query or "",
                att["text"],
                audit_context=audit_context,
            )
        file_id = str(att.get("file_id") or "")
        identity = (
            f"（file_id={file_id}；这是用户实际上传的同一份文件，读取或修改必须使用此 ID，"
            "不得按文件名从“我的文件”选择替代品）"
            if file_id else ""
        )
        block = f"【附件：{att['filename']}】{identity}\n{relevant}"
        if used + len(block) > TOTAL_CONTEXT_BUDGET:
            remain = TOTAL_CONTEXT_BUDGET - used
            if remain > 400:
                blocks.append(block[:remain] + "\n...(附件内容因预算截断)")
                used = TOTAL_CONTEXT_BUDGET
            else:
                skipped += 1
            continue
        blocks.append(block)
        used += len(block)
    if skipped:
        blocks.append(f"（另有 {skipped} 个附件因上下文预算未注入，可让用户追问具体文件）")
    header = (
        "（以下是本会话中用户上传过的文件，整个会话期间都可引用。仅当用户当前的问题确实需要用到它时"
        "才引用或据此作答；无关的寒暄或与文件无关的问题，不要主动复述、总结文件内容。"
        "附件带 file_id 时可直接读取；用户要求修改时在该 file_id 上原位更新并保留版本历史，"
        "只有用户明确要求另做一份时才创建新文件。）"
    )
    uploaded = header + "\n\n" + "\n\n".join(blocks)
    # 引用会话块 + 上传附件块 + 本会话生成物块，并存时一并注入
    return _join_blocks(ref_block, uploaded, generated_block)
