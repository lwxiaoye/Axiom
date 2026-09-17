"""技能运行时：把技能包部署进容器沙箱并执行 entrypoint（对齐蓝本 injectAgentSkillFilesToSandbox
+ runAgentSkillVersionEntrypoints），并把沙箱能力包装成 Agent 可 function-call 的工具。

Agent 执行流程：
  1. 挂载技能含脚本（hasScripts）-> create_configured_sandbox() + sandbox.create()；
     沙箱故障由调用方明确失败（ADR-043），不降级说明书注入
  2. deploy_skills：全部挂载技能的文件树注入 /workspace/skills/<versionId>/（含纯说明型的
     资源文件），存在 entrypoint.sh 则执行（setup）；文件注入失败抛出（对齐蓝本 prepare 链
     throw），entrypoint 失败仅记录（对齐蓝本 continue 语义）
  3. build_sandbox_tools：run_shell / read_file / write_file 作为工具交给模型
  4. 会话结束 sandbox.delete()
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from app.core.config import settings
from app.services.sandbox import ExecuteOptions, SandboxAdapter, SandboxError

_SKILLS_ROOT = "/workspace/skills"


@dataclass
class DeployedSkill:
    skill_id: str
    version_id: str
    name: str
    path: str  # 沙箱内技能目录
    entrypoint_ran: bool = False
    setup_log: str = ""
    error: str = ""


@dataclass
class SkillDeployment:
    sandbox: SandboxAdapter
    skills: list[DeployedSkill] = field(default_factory=list)


async def deploy_skills(sandbox: SandboxAdapter, packages: list[dict]) -> list[DeployedSkill]:
    """把技能包写入沙箱并跑 entrypoint。packages 来自 agent_skill.load_skill_packages。"""
    deployed: list[DeployedSkill] = []
    for pkg in packages:
        version_id = str(pkg.get("versionId") or pkg.get("skillId"))
        skill_dir = f"{_SKILLS_ROOT}/{version_id}"
        rel_prefix = f"skills/{version_id}/"
        files = pkg.get("files") or {}
        # 注入文件（相对 /workspace）
        prefixed = {rel_prefix + rel: data for rel, data in files.items()}
        item = DeployedSkill(
            skill_id=str(pkg.get("skillId")),
            version_id=version_id,
            name=str(pkg.get("name") or version_id),
            path=skill_dir,
        )
        try:
            if prefixed:
                await sandbox.write_file_map(prefixed)
        except SandboxError as exc:
            # 文件注入失败=技能实际不可用，抛给调用方明确失败（ADR-043），不再记 error 继续
            raise SandboxError(f"技能「{item.name}」文件注入失败: {exc}") from exc

        # 执行 entrypoint.sh（setup：装依赖等），失败不致命，记录日志
        entrypoint = pkg.get("entrypoint")
        if entrypoint:
            try:
                result = await sandbox.execute(
                    f"cd {_shq(skill_dir)} && chmod +x {_shq(entrypoint)} 2>/dev/null; "
                    f"bash {_shq(entrypoint)}",
                    ExecuteOptions(timeout_ms=settings.SKILL_SANDBOX_DEPLOY_TIMEOUT * 1000),
                )
                item.entrypoint_ran = True
                item.setup_log = (result.stdout + ("\n" + result.stderr if result.stderr else ""))[:2000]
                if not result.ok:
                    item.error = f"entrypoint 退出码 {result.exit_code}"
            except SandboxError as exc:
                item.error = f"entrypoint 执行失败: {exc}"
        deployed.append(item)
    return deployed


def build_skill_system_prompt(deployed: list[DeployedSkill], contents: list[dict]) -> str:
    """技能指引注入 system prompt：说明书正文 + 沙箱内文件位置 + 可用工具。

    contents 来自 load_skill_contents（含 SKILL.md 正文）。
    """
    if not deployed:
        return ""
    content_map = {c["skillId"]: c for c in contents}
    blocks: list[str] = [
        "你有一个 Linux 沙箱环境，可通过 run_shell / read_file / write_file 工具执行命令与读写文件。",
        "如果要向用户交付文件，请将最终文件写入 /workspace/outputs/（或 /workspace/files/）；运行结束时系统会只发布这些目录中符合交付物规则的文件。不要把脚本、缓存或中间材料放入这两个目录。",
        "以下技能已部署到沙箱，需要时用工具读取其资源文件或执行其脚本（技能目录已列出）：",
    ]
    for item in deployed:
        doc = content_map.get(item.skill_id)
        header = f"### 技能：{item.name}\n目录：{item.path}"
        if item.error:
            header += f"\n（部署告警：{item.error}）"
        body = (doc or {}).get("content") if doc else ""
        blocks.append(header + (f"\n{body}" if body else ""))
    return "\n\n".join(blocks)


def build_sandbox_tools(sandbox: SandboxAdapter):
    """把沙箱能力包装为 ToolSpec 列表（run_shell / read_file / write_file）。"""
    from app.services.agents.agent_executor import ToolSpec

    async def run_shell(args: dict) -> str:
        command = str(args.get("command") or "").strip()
        if not command:
            return "错误：command 为空"
        result = await sandbox.execute(command)
        parts = []
        if result.stdout:
            parts.append(result.stdout)
        if result.stderr:
            parts.append(f"[stderr]\n{result.stderr}")
        parts.append(f"[exit={result.exit_code}]")
        return "\n".join(parts) or "（无输出）"

    async def read_file(args: dict) -> str:
        path = str(args.get("path") or "").strip()
        if not path:
            return "错误：path 为空"
        if not path.startswith("/"):
            path = f"/workspace/{path}"
        try:
            data = await sandbox.read_file(path)
        except SandboxError as exc:
            return f"读取失败：{exc}"
        return data.decode("utf-8", errors="replace")[:8000]

    async def write_file(args: dict) -> str:
        path = str(args.get("path") or "").strip()
        content = args.get("content")
        if not path:
            return "错误：path 为空"
        rel = path.lstrip("/")
        if rel.startswith("workspace/"):
            rel = rel[len("workspace/"):]
        data = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        try:
            await sandbox.write_file_map({rel: data.encode("utf-8")})
        except SandboxError as exc:
            return f"写入失败：{exc}"
        return f"已写入 /workspace/{rel}"

    return [
        ToolSpec(
            name="run_shell",
            description="在 Linux 沙箱中执行 shell 命令（bash），返回 stdout/stderr/退出码。用于运行技能脚本、处理数据、调用工具。",
            parameters={
                "type": "object",
                "properties": {"command": {"type": "string", "description": "要执行的 shell 命令"}},
                "required": ["command"],
            },
            execute=run_shell,
        ),
        ToolSpec(
            name="read_file",
            description="读取沙箱内文件内容（相对路径基于 /workspace）。",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string", "description": "文件路径"}},
                "required": ["path"],
            },
            execute=read_file,
        ),
        ToolSpec(
            name="write_file",
            description="向沙箱写入文件（相对路径基于 /workspace）。要交付给用户的最终文件请写入 outputs/ 或 files/；脚本和中间材料请写在其他目录。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "content": {"type": "string", "description": "文件内容"},
                },
                "required": ["path", "content"],
            },
            execute=write_file,
        ),
    ]


def _shq(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"
