"""幻灯片手改 → 直接重新编译成 PPTX（2026-07-30 用户拍板）。

**为什么有这个模块**：在此之前，编辑器里点「保存修改」做的是
`chatInput = 一段重编译指令; sendChat()` —— 把一段给模型看的内部指令
（read_file / page-01.html / build_deck.py）塞进主对话正文再发出去。用户视角：
点了「保存」，对话里冒出一段看不懂的技术指令，然后等模型跑一轮。四个问题：
「保存」这个词承诺的是存下来、实际是发起一轮 AI 生成；实现细节暴露；无进度预期；
改三页保存三次就是三轮完整重编译。用户原话是「保存修改就直接保存到我的文件中啊」。

**为什么必须回沙箱**：PPTX 是二进制，编辑器手改的是 HTML 编辑源，两者之间隔着技能包里的
编译管线（`scripts/build_deck.py` → `html_to_pptx.py`）。agent-api 容器里只有 python-pptx，
没有技能包也没有它依赖的那套渲染工具，所以只能把编译放进挂了技能包的沙箱里跑。

**为什么是同步端点而不是异步任务**：nginx 对 `/agent-api` 配的是
`proxy_read_timeout 3600s`（见 deploy/nginx.local.conf），编译几十秒到两分钟都在预算内，
不需要引入任务表 + 轮询这一整套。前端拿 loading 等即可。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from app.core.config import settings
from app.services.files import user_file_service
from app.services.files.user_file_service import UserFileError

logger = logging.getLogger(__name__)

# 与前端 utils/slidesRecompile.ts 及后端 chat/tools/paths.py 同一个挂载点
WORKSPACE_FILES = "/workspace/files"
# 技能包在沙箱里的挂载根（见 sandbox_executor 的 skill_packages 说明）
SKILLS_ROOT = "/workspace/skills"
# 编译时长上限：LibreOffice/渲染这类管线几十秒起步，给足 4 分钟仍远小于 nginx 的 3600s
COMPILE_TIMEOUT_MS = 240_000


class SlidesCompileError(Exception):
    """带 status_code 的编译失败，message 直接给用户看。"""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _deck_base(deck_filename: str) -> str:
    return re.sub(r"\.pptx?$", "", deck_filename, flags=re.IGNORECASE)


def _build_script(slides_rel: str, deck_base: str, deck_filename: str) -> str:
    """沙箱内执行的编译脚本。

    刻意写得「话很多」：每一步都 print，失败时 stdout/stderr 会原样回到接口的 detail 里。
    这条链路没有模型在中间兜底解释，出错时用户只能看到我们打出来的这几行。
    """
    return f'''
import glob, json, os, subprocess, sys

SRC = {json.dumps(f"{WORKSPACE_FILES}/{slides_rel}")}
OUT_DIR = "/workspace/slides"
DECK = {json.dumps(deck_filename)}

if not os.path.exists(SRC):
    print("EDIT_SOURCE_MISSING", SRC); sys.exit(11)

try:
    pages = json.load(open(SRC, encoding="utf-8"))
except Exception as e:
    print("EDIT_SOURCE_BAD_JSON", e); sys.exit(12)
if not isinstance(pages, list) or not pages:
    print("EDIT_SOURCE_EMPTY"); sys.exit(13)

os.makedirs(OUT_DIR, exist_ok=True)
# 先清掉上一轮残留的 page-*.html：页数变少时旧文件会被一起编进去，多出几页幽灵内容
for old in glob.glob(os.path.join(OUT_DIR, "page-*.html")):
    try: os.remove(old)
    except OSError: pass
for i, html in enumerate(pages, 1):
    with open(os.path.join(OUT_DIR, "page-%02d.html" % i), "w", encoding="utf-8") as f:
        f.write(html if isinstance(html, str) else str(html))
print("PAGES_WRITTEN", len(pages))

# 技能入口：ppt studio 的包结构是 scripts/build_deck.py（日志实证）。这里做发现而不是
# 写死路径——slug 带哈希后缀（ppt_studio_ad8f7f），且将来换技能包时目录名会变。
cands = sorted(glob.glob("{SKILLS_ROOT}/*/scripts/build_deck.py")) \\
     or sorted(glob.glob("{SKILLS_ROOT}/*/build_deck.py")) \\
     or sorted(glob.glob("{SKILLS_ROOT}/*/**/build_deck.py", recursive=True))
if not cands:
    have = sorted(glob.glob("{SKILLS_ROOT}/*"))
    print("NO_BUILD_ENTRY", "skills=", have); sys.exit(21)
entry = cands[0]
print("ENTRY", entry)

r = subprocess.run([sys.executable, entry, "--name", {json.dumps(deck_base)}],
                   capture_output=True, text=True, cwd="/workspace")
sys.stdout.write(r.stdout[-4000:])
sys.stderr.write(r.stderr[-4000:])
if r.returncode != 0:
    print("BUILD_FAILED rc=%s" % r.returncode); sys.exit(22)

# 产物落点在不同版本的技能包里不完全一致，按优先级找：outputs/ → workspace 根 → slides/
hits = []
for pat in ("/workspace/outputs/*.pptx", "/workspace/*.pptx", os.path.join(OUT_DIR, "*.pptx")):
    hits.extend(sorted(glob.glob(pat)))
if not hits:
    print("NO_PPTX_PRODUCED"); sys.exit(23)
# 同名优先，否则取最新那个
same = [p for p in hits if os.path.basename(p) == DECK]
src = same[0] if same else max(hits, key=os.path.getmtime)
dst = os.path.join({json.dumps(WORKSPACE_FILES)}, DECK)
os.makedirs(os.path.dirname(dst), exist_ok=True)
with open(src, "rb") as fi, open(dst, "wb") as fo:
    fo.write(fi.read())
print("DECK_READY", os.path.basename(src), "->", DECK, os.path.getsize(dst), "bytes")
'''


async def compile_slides_to_deck(
    user_id: str,
    token: str,
    *,
    slides_filename: str,
    deck_filename: str,
    thread_id: Optional[str] = None,
) -> dict:
    """把编辑源（.slides.json）在沙箱里编译成 PPTX，并覆盖写回「我的文件」。

    返回 {"file": {...}, "log": "..."}；失败抛 SlidesCompileError（message 面向用户）。
    """
    from app.services.sandbox.sandbox_executor import execute_in_sandbox
    from app.services.skills import ppt_policy
    from app.services.skills.skill_package_bridge import fetch_skill_packages, is_unavailable

    slides_filename = (slides_filename or "").strip()
    deck_filename = (deck_filename or "").strip()
    if not slides_filename or not deck_filename:
        raise SlidesCompileError("缺少编辑源或目标文件名")
    if not deck_filename.lower().endswith((".ppt", ".pptx")):
        raise SlidesCompileError("目标文件不是 PPT")

    # ① 目标文件必须已存在——这条链路只做「覆盖已有 PPT」，不负责凭空建新文件
    deck_row = await _find_named(user_id, deck_filename)
    if deck_row is None:
        raise SlidesCompileError(f"没有找到《{deck_filename}》，无法覆盖保存", status_code=404)

    # ② 找 PPT 技能并取包。取不到就明确失败——没有技能包就没有编译管线，
    #    与其跑一趟沙箱再报 NO_BUILD_ENTRY，不如在这里就把原因说清楚。
    skills = await _deployed_skill_records(token)
    ppt_id = ppt_policy.find_ppt_skill_id(skills)
    if not ppt_id:
        raise SlidesCompileError(
            "没有启用可用的 PPT 技能，无法直接重新生成；请在 Skill 广场启用 ppt studio 后重试",
            status_code=409,
        )
    record = next((s for s in skills if str(s.get("id") or s.get("record_id")) == str(ppt_id)), None)
    packages = await fetch_skill_packages(
        [{"record_id": str(ppt_id), "name": str((record or {}).get("name") or "ppt studio")}],
        token,
    )
    usable = [p for p in packages if not is_unavailable(p) and (p.get("files") or {})]
    if not usable:
        raise SlidesCompileError("PPT 技能包这次没能取到，请稍后重试", status_code=503)

    # ③ 沙箱里编译。workspace_files 只镜像编辑源这一个文件：整份用户文件区可能很大，
    #    而编译只需要它——镜像越小，容器准备越快，也不会把无关文件暴露进沙箱。
    _, slides_bytes = await _read_named(user_id, slides_filename)
    script = _build_script(slides_filename, _deck_base(deck_filename), deck_filename)
    result = await execute_in_sandbox(
        script,
        language="python",
        skill_packages=usable,
        workspace_files={slides_filename: slides_bytes},
        collect_workspace=True,
        migrate_outputs=True,
        collect_outputs=False,
        timeout_ms=COMPILE_TIMEOUT_MS,
        task_brief=f"重新编译 {deck_filename}",
    )
    log = f"{result.stdout or ''}\n{result.stderr or ''}".strip()
    if not result.ok:
        logger.warning("slides 编译失败 user=%s deck=%s err=%s log=%s",
                       user_id, deck_filename, result.error, log[-800:])
        raise SlidesCompileError(_friendly_error(result.error, log), status_code=500)

    # ④ 取回产物字节并覆盖落库（保持 file_id 不变 → 版本历史里能回滚到手改前）
    data = _pick_deck_bytes(result.workspace_changes or [], deck_filename)
    if data is None:
        logger.warning("slides 编译无产物 user=%s deck=%s log=%s", user_id, deck_filename, log[-800:])
        raise SlidesCompileError(_friendly_error(None, log), status_code=500)

    saved = await user_file_service.overwrite_file(
        user_id, str(deck_row["id"]), data,
        filename=deck_filename,
        thread_id=thread_id or deck_row.get("threadId") or None,
        change_summary="按页面手动编辑重新生成",
        created_by="user",
    )
    return {"file": saved, "log": log[-2000:]}


def _pick_deck_bytes(changes: list, deck_filename: str) -> Optional[bytes]:
    """从 workspace 变更里挑出目标 PPTX。

    同名优先；没有同名再退而求其次取任意 .pptx —— 有些技能版本会在文件名上加后缀，
    这时宁可拿到那一份也好过报「没有产物」让用户白等一轮。
    """
    target = deck_filename.lower()
    same = [c for c in changes if str(c.get("path", "")).lower() == target]
    if same:
        return same[0].get("data")
    any_ppt = [c for c in changes if str(c.get("path", "")).lower().endswith((".pptx", ".ppt"))]
    return any_ppt[0].get("data") if any_ppt else None


def _friendly_error(error: Optional[str], log: str) -> str:
    """把沙箱里的哨兵码翻译成人话。这条链路没有模型兜底，错误文案就是用户唯一能看到的东西。"""
    marks = {
        "EDIT_SOURCE_MISSING": "编辑源没同步进沙箱，请重试一次",
        "EDIT_SOURCE_BAD_JSON": "编辑源内容损坏，无法解析",
        "EDIT_SOURCE_EMPTY": "编辑源是空的，没有可编译的页面",
        "NO_BUILD_ENTRY": "当前 PPT 技能包里没有编译入口（scripts/build_deck.py），无法直接重新生成",
        "BUILD_FAILED": "技能的编译脚本执行失败",
        "NO_PPTX_PRODUCED": "编译脚本没有产出 PPTX",
    }
    for mark, text in marks.items():
        if mark in log:
            return text
    if error:
        return f"重新生成失败：{error}"
    return "重新生成失败，请稍后重试"


async def _find_named(user_id: str, filename: str) -> Optional[dict]:
    """按文件名取一条。

    **直接复用** `chat/tools/paths._find_row` 而不是自己再写一遍：它带着两条来之不易的口径
    ——`list_files(user_id, "__all__")` 才能看到已归入文件夹的文件，以及同名时的取舍必须与
    沙箱镜像 `workspace_sync.prepare()` 一致，否则会出现「一边解析 A、一边回写 B」。
    复制一份的结局是迟早只改一处（这个项目已经在提示词上栽过一次）。
    """
    from app.services.chat.tools.paths import _find_row

    return await _find_row(user_id, filename)


async def _read_named(user_id: str, filename: str) -> tuple[str, bytes]:
    row = await _find_named(user_id, filename)
    if row is None:
        raise SlidesCompileError(f"没有找到编辑源《{filename}》", status_code=404)
    try:
        _, data = await user_file_service.read_bytes(user_id, str(row["id"]))
    except UserFileError as e:
        raise SlidesCompileError(str(e), status_code=e.status_code)
    return str(row["id"]), data


async def _deployed_skill_records(token: str) -> list:
    """agent-api 自持的权威技能目录（enabled=1，ACL 由 token 决定），与主对话选技能同一个来源。"""
    from app.services.chat.turn_context_builder import _get_catalog_records

    try:
        return await _get_catalog_records(token)
    except Exception as e:  # noqa: BLE001 — 目录抖动不该把编译整条打死，交由上层报"没有技能"
        logger.warning("取技能目录失败 err=%s", e)
        return []
