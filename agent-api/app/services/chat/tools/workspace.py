"""技能装载工具（结构手术 Phase 1b 起的「工作区」模块；2026-07-29 收敛为单一职责）。

对模型只暴露一个工具：`use_skill` —— 即时启用 Skill 广场里的某个技能（回源权威 SKILL.md，
并把它自带的脚本/资源挂进沙箱 `/workspace/skills/`）。

另一半职责是 `exports`：把技能包解析器交给同批构建的 `bash`。这是**技能文件挂进沙箱的唯一
通道** —— 当前沙箱挂载仍由本模块合并「用户 @ 选中的包 + use_skill 即时加载的包」，
但 Skill 身份与取包结果同时写入 RunState，恢复段按持久化事实重新 ACL/取包，不依赖进程闭包。

职责边界：文件的寻址与读写在 `paths.py`（模型只见路径，`/workspace/files` 就是「我的文件」
的镜像），沙箱执行在 `shell.py`。ACL 全部在 user_file_service 按 user_id 锁死。
"""
import logging
import hashlib
import re
from typing import Any, Awaitable, Callable, List, Optional

from .base import INTENT_PROP, MainTool, ToolSoftError, ToolValue, text_tool_body

logger = logging.getLogger(__name__)


def _is_unavailable(pkg) -> bool:
    """取包失败的占位记录（见 skill_package_bridge._unavailable_pkg）：不可挂载、不可匹配。"""
    return bool(isinstance(pkg, dict) and pkg.get("unavailable"))


def _pkg_key(pkg: dict) -> str:
    return str(pkg.get("recordId") or pkg.get("skillId") or pkg.get("slug") or pkg.get("name") or "")


def _package_digest(package: Optional[dict]) -> Optional[str]:
    """Digest the actual text files returned by the package bridge for recovery identity."""
    files = (package or {}).get("files") if isinstance(package, dict) else None
    if not isinstance(files, dict) or not files:
        return None
    digest = hashlib.sha256()
    normalized_files = {str(key): value for key, value in files.items()}
    for name in sorted(normalized_files):
        value = normalized_files[name]
        if isinstance(value, bytes):
            data = value
        elif isinstance(value, bytearray):
            data = bytes(value)
        else:
            data = str(value or "").encode("utf-8")
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return digest.hexdigest()


def _skill_state_record(
    skill: Optional[dict],
    *,
    selection_source: str,
    selection_method: str,
    status: str,
    retryable: bool = False,
    last_error: str = "",
    package: Optional[dict] = None,
) -> Optional[dict]:
    """Build a durable fact record from trusted ACL/package responses."""
    from app.services.chat.turn_context_builder import skill_state_record_from_skill

    package_facts = dict(package or {})
    if package_facts and not package_facts.get("packageDigest"):
        package_facts["packageDigest"] = _package_digest(package_facts)
    return skill_state_record_from_skill(
        dict(skill or {}),
        selection_source=selection_source,
        selection_method=selection_method,
        status=status,
        retryable=retryable,
        last_error=last_error,
        package=package_facts,
    )


def _pick_unreported(pkgs: list, seen: Optional[set]) -> list:
    """只留下还没在本轮回执里提过的包（seen 由 use_skill 与 bash 共享，见 exports）。

    不去重的话：@ 选中 3 个技能时每一次 bash 调用都会把同一段"××没挂上来"重复一遍，
    真正的告警会被自己的噪音稀释。
    """
    out = []
    for pkg in pkgs or []:
        if not isinstance(pkg, dict):
            continue
        if seen is None:
            out.append(pkg)
            continue
        key = _pkg_key(pkg)
        if key in seen:
            continue
        seen.add(key)
        out.append(pkg)
    return out


def _unavailable_note(pkgs: list, seen: Optional[set] = None) -> str:
    """取包失败的技能**必须点名说清楚本轮不可用**（2026-07-28）。

    此前这条路整个是哑的：Java `/ai/skill/files|file` 一抖动，`fetch_skill_packages` 静默
    返回空列表，而系统提示词已经写着「它们已挂在沙箱内 /workspace/skills/ 下」、时间线也
    发了一对 use_skill 事件说「自带脚本已挂入沙箱」。模型于是去跑一个不存在的脚本，
    或者更糟——直接假装跑过了。ADR-043 对含脚本技能的要求是**不可降级、必须明确失败**，
    工作流侧一路 raise，主对话侧此前一处都没有。
    """
    bits = []
    for pkg in _pick_unreported([p for p in (pkgs or []) if _is_unavailable(p)], seen):
        name = str(pkg.get("name") or pkg.get("skillId") or "未知技能")
        reason = str(pkg.get("reason") or "取包失败")
        if pkg.get("hasScripts"):
            known = "" if pkg.get("scriptsKnown") else "（连文件树都没取到，按最坏情况处理）"
            bits.append(
                f"\n⚠️ 技能《{name}》**本轮没有挂进沙箱**（{reason}）{known}。"
                f"/workspace/skills/ 下**没有**它的任何文件——不要去执行它的脚本、"
                "不要假装执行过、也不要向用户宣称用了这个技能。"
                "上下文里的 SKILL.md 只能当参考读；若它的步骤依赖自带脚本，"
                "如实告诉用户这个技能暂时不可用，别猜脚本内容自己重写一份。"
            )
        else:
            # 纯说明书类技能（树里没有任何脚本）：说明书已在上下文里，可以继续降级使用
            bits.append(
                f"\n注意：技能《{name}》的附带文件本轮没能取到（{reason}），"
                "但它不含可执行脚本——上下文里的说明书照常适用，按说明书做即可。"
            )
    return "".join(bits)


def _unmounted_note(pkgs: list, seen: Optional[set] = None) -> str:
    """技能包里没挂上来的东西**必须告诉模型**（2026-07-27）。

    取包通道走 Java 的 JSON 接口，二进制文件的字节在服务端就已经丢了（拿回来的是被解码过的
    字符串），所以 png/字体/zip 一律不挂 —— 挂一个坏掉的比不挂更糟。文件数与字节预算的截断
    同理。不说的话，模型会按 SKILL.md 去引用一个不存在的文件，然后在"文件不存在"里反复打转，
    完全猜不到是平台没挂上来（12k 文件的包只挂前 200 个就是这个形态）。

    ⚠️ 调用点不止 use_skill（2026-07-28）：@ 选中与 PPT 自动预加载进来的包同样带 unmounted
    字段，此前**没有任何代码读它**——而 ppt-studio 走的正是自动预加载这条路。现在 bash 在
    首次挂载时也会念一遍，seen 保证同一个包只说一次。
    """
    binary: list = []
    truncated: list = []
    fetch_failed: list = []
    for pkg in _pick_unreported(pkgs, seen):
        um = pkg.get("unmounted") or {}
        binary.extend(um.get("binary") or [])
        truncated.extend((um.get("over_file_limit") or []) + (um.get("over_byte_budget") or []))
        fetch_failed.extend(um.get("fetch_failed") or [])
    bits = []
    if binary:
        head = "、".join(binary[:4])
        more = f" 等 {len(binary)} 个" if len(binary) > 4 else ""
        # 只陈述事实，**不给替代方案**。初版写的是「需要图就用 search_web + download_url
        # 另取」——那是平台在猜某个技能该拿什么替代什么，违反同日刚立的 A/B 分层口径；
        # 而且对字体是**错的**：ttf 下进「我的文件」在沙箱里同样用不上（PPTX 的字体必须是
        # 打开文件的机器上已安装的）。怎么替代由该技能的 SKILL.md 说。
        bits.append(
            f"\n注意：该技能包里的图片/字体/压缩包等**二进制资源没有挂进沙箱**"
            f"（{head}{more}）——取包通道没有拿到完整字节，包完整性为 incomplete。"
            "不要引用它们，也不要因为找不到就反复重试。"
            "SKILL.md 里若给了不依赖这些资源的做法，按它执行；没有就如实告诉用户这部分做不了。"
        )
    if truncated:
        head = "、".join(truncated[:4])
        more = f" 等 {len(truncated)} 个" if len(truncated) > 4 else ""
        bits.append(
            f"\n注意：该技能包超出单次挂载上限，以下文件**没有挂进沙箱**（{head}{more}）。"
            "如果 SKILL.md 的步骤依赖它们，请如实告诉用户这一步做不了，不要猜内容。"
        )
    if fetch_failed:
        head = "、".join(fetch_failed[:4])
        more = f" 等 {len(fetch_failed)} 个" if len(fetch_failed) > 4 else ""
        bits.append(
            f"\n注意：该技能包有文件本轮回源失败，未挂进沙箱（{head}{more}）。"
            "不要假装这些文件存在；平台不会缓存这份残缺结果，后续回合可重新加载。"
        )
    return "".join(bits)


def build_workspace_tools(*, user_id: str, token: str = "",
                          run_id: Optional[str] = None,
                          tool_meta_sink: Optional[dict] = None,
                          skill_packages_provider: Optional[Callable[[], Awaitable[list]]] = None,
                          loaded_skills: Optional[List[dict]] = None,
                          selected_skills: Optional[List[dict]] = None,
                          skill_state: Optional[List[dict]] = None,
                          user_message: Optional[str] = None,
                          runtime_profile: Optional[dict] = None,
                          allow_dynamic_profile: bool = False,
                          # 导出口（2026-07-27）：把技能包解析器交给同批的 bash 工具。
                          # 只有本模块持有 @ 选中 + use_skill 即时加载的合并清单，
                          # 不导出的话技能包不会被挂进沙箱 —— Skill 直接失效（实测踩到）。
                          exports: Optional[dict] = None) -> List[MainTool]:
    # user_id 是本模块的注册前提（调用方按登录态开这一族工具），技能 ACL 由 Java 侧按
    # token 校验，故此处不再直接用它取文件。
    tools: List[MainTool] = []

    # 选中 skill 的文件包懒取一次、整轮缓存（provider 回源 Java /ai/skill/files|file，可能慢）
    _skill_pkg_cache: dict = {}
    # use_skill 即时加载的技能包（Phase 2）：模型自己从目录挑技能→本工具取包追加到这里，
    # bash 挂载时并入挂载集，无需用户手动 @ 选中。恢复得到的 model/use_skill 记录不能
    # 走当前进程的幂等短路，必须重新取包。
    _dynamic_skill_pkgs: list = []
    _loaded_skill_records = [item for item in (loaded_skills or []) if isinstance(item, dict)]
    _selected_skill_by_id = {
        str(item.get("id") or item.get("skill_id") or "").strip(): item
        for item in (selected_skills or [])
        if isinstance(item, dict) and str(item.get("id") or item.get("skill_id") or "").strip()
    }
    _skill_state_by_id = {
        str(item.get("skill_id") or item.get("id") or "").strip(): item
        for item in (skill_state or [])
        if isinstance(item, dict)
        and str(item.get("skill_id") or item.get("id") or "").strip()
    }

    def _activate_runtime_profile(skill: Optional[dict]) -> None:
        """Switch the live tool closures after a verified model ``use_skill`` success."""
        if not allow_dynamic_profile or not isinstance(runtime_profile, dict):
            return
        from app.services.chat.execution_profile import dynamic_profile_for_skill

        next_profile = dynamic_profile_for_skill(runtime_profile, skill or {})
        if str(next_profile.get("id") or "") != "artifact_coding":
            return
        runtime_profile.clear()
        runtime_profile.update(next_profile)

    def _state_source(skill_id: str) -> str:
        state = _skill_state_by_id.get(str(skill_id or "").strip()) or {}
        source = str(state.get("selection_source") or "").strip().lower()
        method = str(state.get("selection_method") or "").strip().lower()
        if source in {"explicit", "user", "selected"}:
            return "explicit"
        return "model" if source in {"model", "model/use_skill", "dynamic"} or method == "use_skill" else "explicit"

    def _selection_source(skill_id: str) -> str:
        normalized_id = str(skill_id or "").strip()
        if normalized_id in _selected_skill_by_id:
            return "explicit"
        if normalized_id in _skill_state_by_id:
            return _state_source(normalized_id)
        # No explicit selection or restored fact exists: this call is the
        # model's autonomous discovery path by definition.
        return "model"

    _loaded_skill_ids: set = {
        str(item.get("id") or "").strip()
        for item in _loaded_skill_records
        if str(item.get("id") or "").strip()
        # A restored model/use_skill fact proves a previous segment loaded the
        # package, not that this process has mounted it. Force use_skill through
        # ACL/package revalidation so the current Run can rebuild the mount.
        and _state_source(str(item.get("id") or "").strip()) != "model"
    }

    def _normalized_skill_identity(value: Any) -> str:
        return re.sub(r"[\s_]+", "-", str(value or "").strip().lower())

    # The Java Skill catalog may use an opaque record identity (for example
    # ``extract_...``) while the first-party execution profile intentionally
    # refers to the stable product identity ``ppt-studio``.  Keep canonical
    # ids as the persistence authority, but register trusted semantic aliases
    # only after that exact catalog record has really passed ACL/package load.
    # This lets the same Run unlock its PPT tools without treating a merely
    # selected Skill, a user phrase, or a broad "ppt" match as loaded.
    _loaded_skill_aliases: set[str] = {
        _normalized_skill_identity(skill_id)
        for skill_id in _loaded_skill_ids
        if _normalized_skill_identity(skill_id)
    }

    def _register_loaded_skill(skill: Optional[dict], *, canonical_id: str = "") -> None:
        identity = str(canonical_id or "").strip()
        if identity:
            _loaded_skill_ids.add(identity)
            _loaded_skill_aliases.add(_normalized_skill_identity(identity))
        if not isinstance(skill, dict):
            return
        from app.services.chat.execution_profile import is_first_party_ppt_skill

        if is_first_party_ppt_skill(skill):
            _loaded_skill_aliases.add("ppt-studio")

    def _is_skill_loaded(skill_id: str) -> bool:
        normalized = _normalized_skill_identity(skill_id)
        return bool(normalized) and normalized in _loaded_skill_aliases

    for _loaded_record in _loaded_skill_records:
        _loaded_id = str(_loaded_record.get("id") or _loaded_record.get("skill_id") or "").strip()
        if _loaded_id in _loaded_skill_ids:
            _register_loaded_skill(_loaded_record, canonical_id=_loaded_id)

    # 已经在本轮回执里提过「没挂上/没取到」的技能（按 recordId/skillId 去重）。
    # use_skill 与 bash 共享同一个集合，同一个包只念一遍。
    _skill_notice_seen: set = set()

    async def _persist_skill_fact(
        skill: Optional[dict],
        *,
        selection_source: str,
        selection_method: str,
        status: str,
        retryable: bool = False,
        last_error: str = "",
        package: Optional[dict] = None,
        fail_closed: bool = False,
    ) -> bool:
        if not run_id:
            return True
        record = _skill_state_record(
            skill,
            selection_source=selection_source,
            selection_method=selection_method,
            status=status,
            retryable=retryable,
            last_error=last_error,
            package=package,
        )
        if not record:
            return False
        try:
            from app.services.chat.turn_context_builder import persist_skill_state
            ok = bool(await persist_skill_state(str(run_id), record))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Skill RunState 持久化失败 run=%s skill=%s: %s",
                run_id, record.get("skill_id"), exc,
            )
            ok = False
        if not ok and fail_closed:
            raise ToolSoftError(
                f"技能「{record['skill_id']}」的恢复事实未能保存，当前未确认已加载。",
                code="skill_state_persist_failed",
                retryable=True,
                details={
                    "skill_id": record["skill_id"],
                    "selection_source": record["selection_source"],
                    "selection_method": record["selection_method"],
                    "status": "state_persist_failed",
                },
            )
        return ok

    async def _persist_resolved_packages(packages: list) -> None:
        """Persist package identity/outcome after the bridge returns an actual result."""
        by_id = {
            str(item.get("id") or item.get("skillId") or "").strip(): item
            for item in _loaded_skill_records
            if str(item.get("id") or item.get("skillId") or "").strip()
        }
        for package in packages or []:
            if not isinstance(package, dict):
                continue
            skill_id = str(package.get("skillId") or package.get("id") or "").strip()
            skill = by_id.get(skill_id) or {
                "id": skill_id,
                "record_id": package.get("recordId"),
                "name": package.get("name") or skill_id,
            }
            unavailable = _is_unavailable(package)
            reason = str(package.get("reason") or "取包失败") if unavailable else ""
            source = "model" if _state_source(skill_id) == "model" else "explicit"
            await _persist_skill_fact(
                skill,
                selection_source=source,
                selection_method="use_skill",
                status="package_unavailable" if unavailable else "loaded",
                retryable=unavailable,
                last_error=reason,
                package=package,
            )

    async def _resolve_skill_packages() -> list:
        base: list = []
        if skill_packages_provider is not None:
            if "v" not in _skill_pkg_cache:
                try:
                    _skill_pkg_cache["v"] = await skill_packages_provider() or []
                except Exception as e:  # noqa: BLE001 取包失败不阻断执行
                    # 但**绝不能静默降级成空列表**：调用方据此认为"用户没选技能"，
                    # 而系统提示词已经声称技能脚本挂好了。合成占位记录，让 bash 的回执
                    # 能点名说出这些技能本轮不可用（同 skill_package_bridge 的口径）。
                    logger.warning("取 skill 包失败: %s", e)
                    _skill_pkg_cache["v"] = [
                        {
                            "skillId": str(item.get("id") or ""),
                            "recordId": str(item.get("record_id") or ""),
                            "name": str(item.get("name") or item.get("id") or "未知技能"),
                            "slug": "", "files": {}, "entrypoint": None,
                            # 连取都没取到，含不含脚本未知 → 保守按含脚本报
                            "hasScripts": True, "scriptsKnown": False,
                            "unmounted": {}, "unavailable": True,
                            "reason": f"取包失败（{e}）",
                        }
                        for item in _loaded_skill_records
                    ]
                if not _skill_pkg_cache["v"] and _loaded_skill_records:
                    # A provider returning an empty list for an ACL-approved package is still a
                    # package failure.  Keep a structured unavailable fact instead of allowing
                    # the caller to infer that no Skill was selected.
                    _skill_pkg_cache["v"] = [
                        {
                            "skillId": str(item.get("id") or ""),
                            "recordId": str(item.get("record_id") or ""),
                            "name": str(item.get("name") or item.get("id") or "未知技能"),
                            "slug": "", "files": {}, "entrypoint": None,
                            "hasScripts": True, "scriptsKnown": False,
                            "unmounted": {}, "unavailable": True,
                            "reason": "取包返回空结果",
                        }
                        for item in _loaded_skill_records
                    ]
                await _persist_resolved_packages(_skill_pkg_cache["v"])
            base = _skill_pkg_cache["v"]
        # @ 选中的（base）+ use_skill 即时加载的（dynamic）一并挂载
        return list(base) + _dynamic_skill_pkgs

    if exports is not None:
        exports["resolve_skill_packages"] = _resolve_skill_packages
        exports["skill_notice_seen"] = _skill_notice_seen
        exports["is_skill_loaded"] = _is_skill_loaded

    # use_skill（Phase 2：自主启用技能）——模型从系统提示词「Skill 目录」里挑中合适技能后自己调用，
    # 后端即时校验 ACL（/ai/skill/list enabled）→ 回源完整 SKILL.md 返回给模型 → 取其脚本包挂进
    # 沙箱 /workspace/skills/，之后模型用 bash 按 SKILL.md 执行。全程不用用户手动 @ 选中。
    async def _use_skill(args: dict) -> str:
        skill_id = str(args.get("skill_id") or "").strip()
        if not skill_id:
            raise ToolSoftError("use_skill 需要 skill_id（取自系统提示词「Skill 目录」里每项方括号内的 id）。")
        if _is_skill_loaded(skill_id):
            return (f"技能「{skill_id}」本轮已读取，权威 SKILL.md 已返回；"
                    "不要再用 read_file/glob 读取 skills 路径，也**不要再次 use_skill** 整包重取；"
                    "直接用 bash 按已加载说明执行即可。")
        selection_source = _selection_source(skill_id)
        # 只用 id 回源 Java 校验（ACL+enabled），前端/模型给的名称一律不采信（防越权/注入）
        from app.services.chat.turn_context_builder import _fetch_trusted_skills, _resolve_skill_id
        skills = await _fetch_trusted_skills([skill_id], token)
        if not skills:
            # 模型常把 skill_id 猜成短名/关键词（如把《SVG转PPTX工作流》调成 "pptx"）——精确 id 查不到
            # 时，在权威目录里按 id/名称模糊解析真实 id。唯一命中→自动改用真实 id 重取；多命中→列候选让
            # 模型二选一；无命中→保持「未找到」。这样绝大多数猜错的 id 一次调用就自愈，不再空转一轮。
            resolved, candidates = await _resolve_skill_id(skill_id, token)
            if resolved and resolved != skill_id:
                if _is_skill_loaded(resolved):  # 猜错的 id 解析到的却是本轮已加载的技能
                    return (f"技能「{resolved}」本轮已读取，权威 SKILL.md 已返回；"
                            "不要再用 read_file/glob 读取 skills 路径，直接用 bash 执行即可。")
                skills = await _fetch_trusted_skills([resolved], token)
                if skills:
                    skill_id = resolved
            if not skills:
                if candidates:
                    raise ToolSoftError(
                        f"没有 id 恰为「{skill_id}」的技能。你可能想找下面这些（用方括号里的 id 重新调用 "
                        f"use_skill）：\n" + "\n".join(f"- {c}" for c in candidates)
                    )
                await _persist_skill_fact(
                    {"id": skill_id, "name": skill_id},
                    selection_source=selection_source,
                    selection_method="use_skill",
                    status="acl_unavailable",
                    retryable=True,
                    last_error="未通过权威 ACL/目录校验",
                )
                raise ToolSoftError(
                    f"未找到可用技能「{skill_id}」（不存在 / 已停用 / 无权限）。"
                    "本轮没有加载该技能；请核对 Skill 目录中的真实 id 后重试。",
                    code="skill_acl_unavailable",
                    retryable=True,
                    details={
                        "skill_id": skill_id,
                        "selection_source": selection_source,
                        "selection_method": "use_skill",
                        "status": "acl_unavailable",
                    },
                )
        s = skills[0]
        skill_id = str(s.get("id") or skill_id).strip()
        selection_source = _selection_source(skill_id)
        pkg_note = ""
        await _persist_skill_fact(
            s,
            selection_source=selection_source,
            selection_method="use_skill",
            status="authorized",
            retryable=False,
            fail_closed=True,
        )
        if s.get("record_id"):
            try:
                from app.services.skills import skill_package_bridge
                pkgs = await skill_package_bridge.fetch_skill_packages(
                    [{
                        "id": s.get("id"),
                        "record_id": s["record_id"],
                        "name": s.get("name") or s.get("id"),
                    }],
                    token,
                )
                mounted = [p for p in pkgs if isinstance(p, dict) and not _is_unavailable(p)]
                if not mounted:
                    failed_pkg = next(
                        (p for p in pkgs if isinstance(p, dict)),
                        {
                            "skillId": skill_id,
                            "recordId": s.get("record_id"),
                            "name": s.get("name") or skill_id,
                            "unavailable": True,
                            "hasScripts": True,
                            "scriptsKnown": False,
                            "reason": "取包返回空结果",
                        },
                    )
                    _dynamic_skill_pkgs.append(failed_pkg)
                    reason = str(failed_pkg.get("reason") or "取包失败")
                    await _persist_skill_fact(
                        s,
                        selection_source=selection_source,
                        selection_method="use_skill",
                        status="package_unavailable",
                        retryable=True,
                        last_error=reason,
                        package=failed_pkg,
                        fail_closed=True,
                    )
                    raise ToolSoftError(
                        f"技能「{s.get('name') or skill_id}」未加载：技能包未能挂进沙箱（{reason}）。"
                        "当前没有加载该技能，不能执行其脚本或宣称已使用；可稍后重试。",
                        code="skill_package_unavailable",
                        retryable=True,
                        details={
                            "skill_id": skill_id,
                            "record_id": s.get("record_id"),
                            "version": s.get("version"),
                            "package_id": s.get("package_id") or s.get("record_id"),
                            "selection_source": selection_source,
                            "selection_method": "use_skill",
                            "status": "package_unavailable",
                        },
                    )
                # Keep the selected and dynamic package lists additive; recovery is allowed to
                # re-fetch the same package, while a live process still avoids duplicate mounts.
                _dynamic_skill_pkgs.extend(mounted)
                await _persist_skill_fact(
                    s,
                    selection_source=selection_source,
                    selection_method="use_skill",
                    status="loaded",
                    package=mounted[0],
                    fail_closed=True,
                )
                slug = next((p.get("slug") for p in mounted if p.get("slug")), "")
                pkg_note = (f"其脚本/资源已挂到沙箱 /workspace/skills/{slug}/，"
                            "下面正文就是 Skill 广场返回的权威 SKILL.md；不要再调用 read_file/glob 读取它，"
                            "直接用 bash 按步骤执行（python3 / bash / make 均可）。")
                pkg_note += _unmounted_note(mounted, _skill_notice_seen)
            except Exception as e:  # noqa: BLE001 取包失败不阻断——仍把说明给模型
                if isinstance(e, ToolSoftError):
                    raise
                logger.warning("use_skill 取包失败 %s: %s", skill_id, e)
                reason = f"{type(e).__name__}: {e}"
                await _persist_skill_fact(
                    s,
                    selection_source=selection_source,
                    selection_method="use_skill",
                    status="package_unavailable",
                    retryable=True,
                    last_error=reason,
                    package={
                        "skillId": skill_id,
                        "recordId": s.get("record_id"),
                        "name": s.get("name") or skill_id,
                        "unavailable": True,
                        "hasScripts": True,
                        "scriptsKnown": False,
                        "reason": reason,
                    },
                    fail_closed=True,
                )
                raise ToolSoftError(
                    f"技能「{s.get('name') or skill_id}」未加载：技能包取回失败（{reason}）。"
                    "当前没有加载该技能，不能执行其脚本或宣称已使用；可稍后重试。",
                    code="skill_package_unavailable",
                    retryable=True,
                    details={
                        "skill_id": skill_id,
                        "record_id": s.get("record_id"),
                        "version": s.get("version"),
                        "package_id": s.get("package_id") or s.get("record_id"),
                        "selection_source": selection_source,
                        "selection_method": "use_skill",
                        "status": "package_unavailable",
                    },
                )
        else:
            await _persist_skill_fact(
                s,
                selection_source=selection_source,
                selection_method="use_skill",
                status="loaded",
                fail_closed=True,
            )
            pkg_note = "（该技能没有服务端包记录，当前使用已通过 ACL 校验的说明。）"
        _register_loaded_skill(s, canonical_id=skill_id)
        # Persisted Skill facts are authoritative for the next segment; this in-memory overlay
        # makes the *same* Run immediately use the first-party PPTD closure after use_skill.
        _activate_runtime_profile(s)
        if tool_meta_sink is not None:
            tool_meta_sink["use_skill"] = {
                "action": {"operation": "load", "target": f"技能《{s['name']}》"},
                "skill": {
                    "skill_id": skill_id,
                    "record_id": s.get("record_id"),
                    "version": s.get("version"),
                    "package_id": s.get("package_id") or s.get("record_id"),
                    "selection_source": selection_source,
                    "selection_method": "use_skill",
                    "status": "loaded",
                },
            }
        from app.services.skills.skill_package_bridge import normalize_skill_instructions
        instructions = normalize_skill_instructions(
            s.get("instructions") or s.get("description") or "（该技能无详细说明）",
            skill_name=s.get("name"),
        )
        return f"已读取技能「{s['name']}」。{pkg_note}\n\n【SKILL.md】\n{instructions}"

    tools.append(
        MainTool(
            name="use_skill",
            description=(
                "读取平台 Skill 广场里的某个技能：把它完整的 SKILL.md 说明书返回给你，"
                "并把它自带的脚本/资源挂进沙箱 /workspace/skills/ 下，之后你就能按其方法执行。\n"
                "何时用：系统提示词的「Skill 目录」里有适合当前任务的技能时，直接用其 id 调用本工具，"
                "**不用让用户手动去 @ 选中**。加载后返回内容就是权威说明，不要再调用 read_file/glob"
                " 读取 skills 路径；直接照 SKILL.md 配合 bash 执行其脚本完成任务。\n"
                "用户说「用某某 skill 做…」时，先在目录里按名称/用途对上号，再 use_skill 加载它。"
                "纯用你已有能力就能做完的任务不必调用。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "skill_id": {
                        "type": "string",
                        "description": "要读取的技能 id（取自系统提示词「Skill 目录」里每项方括号内标注的 id）",
                    },
                    "intent": dict(INTENT_PROP),
                },
                "required": ["skill_id"],
            },
            semantic_tags=("skill_load",),
            execute=text_tool_body(_use_skill),
            public_action="调用技能包",
            output_model=ToolValue,
            internal=True,  # 只读加载（取说明+挂包），无用户数据副作用，不经网关审批
        )
    )
    return tools
