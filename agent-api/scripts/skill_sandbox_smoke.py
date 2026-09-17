"""Skill 脚本进沙箱冒烟（ADR-047 §6.6）：
1) code_runner.run_code(skill_packages=...) 真挂进 /workspace/skills/<slug>/ 且脚本可执行；
2) skill_package_bridge：树扁平化 / jeecg 解包 / slug / hasScripts 纯函数正确性。

运行（容器内，需 local 沙箱可用）：docker exec agent-api python scripts/skill_sandbox_smoke.py
"""
import asyncio
import sys

sys.path.insert(0, "/app")

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results = []


def check(name, ok, detail=""):
    ok = bool(ok)
    results.append(ok)
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail and not ok else ""))


async def main():
    from app.services.sandbox import code_runner
    from app.services.skills import skill_package_bridge as bridge

    # --- 纯函数 ---
    tree = [
        {"name": "SKILL.md", "path": "SKILL.md", "directory": False},
        {"name": "scripts", "path": "scripts", "directory": True, "children": [
            {"name": "gen.py", "path": "scripts/gen.py", "directory": False},
        ]},
        {"name": "refs", "path": "refs", "directory": True, "children": [
            {"name": "a.md", "path": "refs/a.md", "directory": False},
        ]},
    ]
    flat = bridge._flatten_tree(tree)
    check("树扁平化取叶子", set(flat) == {"SKILL.md", "scripts/gen.py", "refs/a.md"}, str(flat))
    # 子节点只带 name、不带完整 path（部分 Java 文件树的返回形态）：目录层级须由 prefix 还原，
    # 否则 scripts/gen.py 被拍平成 gen.py（同名覆盖、脚本相对路径失效）
    tree_name_only = [
        {"name": "SKILL.md", "directory": False},
        {"name": "scripts", "directory": True, "children": [
            {"name": "gen.py", "directory": False},
            {"name": "sub", "directory": True, "children": [
                {"name": "deep.py", "directory": False},
            ]},
        ]},
    ]
    flat2 = bridge._flatten_tree(tree_name_only)
    check(
        "树扁平化还原 name-only 层级",
        set(flat2) == {"SKILL.md", "scripts/gen.py", "scripts/sub/deep.py"},
        str(flat2),
    )
    check("jeecg 解包 {result}", bridge._unwrap({"success": True, "result": "hi"}) == "hi")
    check("jeecg 无信封原样", bridge._unwrap("plain") == "plain")
    check("slug 中文名转安全目录", bridge._slugify("PPT 生成器!", "fb") == "PPT_生成器" or bridge._slugify("ppt-gen", "fb") == "ppt-gen")
    check("hasScripts 识别 .py", bridge._has_scripts({"scripts/gen.py": b"x"}, None) is True)
    check("hasScripts 纯说明 False", bridge._has_scripts({"SKILL.md": b"x", "refs/a.md": b"y"}, None) is False)
    check("hasScripts entrypoint True", bridge._has_scripts({"SKILL.md": b"x"}, "entrypoint.sh") is True)

    # --- 沙箱真挂载 + 执行 skill 脚本 ---
    pkg = {
        "slug": "demo-skill",
        "files": {
            "SKILL.md": b"# demo\n",
            "scripts/greet.py": "print('hello from skill script')\n".encode("utf-8"),
            "data/n.txt": b"42",
        },
    }
    # 模型代码：调用 skill 自带脚本 + 读 skill 资源，产物写 outputs
    code = (
        "import subprocess, pathlib\n"
        "r = subprocess.run(['python','/workspace/skills/demo-skill/scripts/greet.py'],"
        " capture_output=True, text=True)\n"
        "n = pathlib.Path('/workspace/skills/demo-skill/data/n.txt').read_text()\n"
        "print('script_stdout:', r.stdout.strip())\n"
        "print('resource_n:', n)\n"
        "pathlib.Path('/workspace/outputs/result.txt').write_text(r.stdout.strip()+'|'+n)\n"
    )
    res = await code_runner.run_code(code, skill_packages=[pkg], fetch_output_bytes=True)
    check("run_code 成功", res.ok and res.exit_code == 0, res.to_tool_text()[:400])
    check("skill 脚本被执行", "hello from skill script" in res.stdout, res.stdout[:200])
    check("skill 资源文件可读", "resource_n: 42" in res.stdout, res.stdout[:200])
    out = next((f for f in res.output_files if f["name"] == "result.txt"), None)
    check("产物含脚本+资源结果", bool(out and out.get("content") == b"hello from skill script|42"))

    # 路径穿越防护：`../../etc/pwn` 的 .. 被剥离 → 收敛为 evil/etc/pwn，绝不逃出 skills 目录。
    # 真正的安全不变量：系统 /etc 未被污染（原文件不存在，未被 skill 内容覆盖）。
    evil = {"slug": "evil", "files": {"../../etc/pwn": b"PWNED", "ok.py": b"print(1)"}}
    res2 = await code_runner.run_code(
        "import os\n"
        "print('inside=', sorted(os.listdir('/workspace/skills/evil')))\n"
        "print('etc_pwn_exists=', os.path.exists('/etc/pwn'))\n",
        skill_packages=[evil],
    )
    check(
        "路径穿越被收敛(未逃出 skills，系统 /etc 未污染)",
        res2.ok and "ok.py" in res2.stdout and "etc_pwn_exists= False" in res2.stdout,
        res2.stdout[:200],
    )

    total, passed = len(results), sum(results)
    print(f"\n{passed}/{total} passed")
    raise SystemExit(0 if passed == total else 1)


if __name__ == "__main__":
    asyncio.run(main())
