"""本地沙箱内核冒烟（ADR-047 §6.5）。可在 host 直接跑（host 有 docker + python3），
不依赖 agent-api 容器挂 socket——验证 LocalDockerAdapter + code_runner 端到端。

运行：cd agent-api && SKILL_SANDBOX_PROVIDER=local python scripts/sandbox_local_smoke.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SKILL_SANDBOX_PROVIDER", "local")

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail and not ok else ""))


async def main():
    from app.services.sandbox.code_runner import run_code
    from app.services.sandbox.factory import sandbox_available

    check("provider 就绪(ping)", await sandbox_available())

    # 1) 基本 stdout
    r = await run_code("print('hello', 1 + 2)")
    check("基本执行拿回 stdout", r.ok and "hello 3" in r.stdout, r.to_tool_text())

    # 2) 数据整理：pandas 算对（LLM 口算的痛点）
    r = await run_code(
        "import pandas as pd\n"
        "df = pd.DataFrame({'学院':['计','数','计','数'],'分':[90,80,70,60]})\n"
        "print(df.groupby('学院')['分'].mean().to_dict())"
    )
    check("pandas 分组聚合", r.ok and "计" in r.stdout and "80.0" in r.stdout, r.to_tool_text())

    # 3) 非零退出 + stderr 回传（供工具循环自修）
    r = await run_code("raise ValueError('boom')")
    check("异常→非零退出 + stderr", (not r.ok) and r.exit_code != 0 and "boom" in r.stderr, r.to_tool_text())

    # 4) 输入文件进沙箱 + 产物出到 outputs
    r = await run_code(
        "open('/workspace/outputs/report.txt','w').write(open('/workspace/inputs/data.txt').read().upper())",
        input_files={"data.txt": "hello world"},
    )
    got = any(f["name"] == "report.txt" for f in r.output_files)
    check("输入文件读入 + 产物落 outputs", r.ok and got, r.to_tool_text())

    # 5) 生成 docx（文档工厂北极星的最小验证）
    r = await run_code(
        "from docx import Document\n"
        "d = Document(); d.add_heading('测试报告', 0); d.add_paragraph('正文')\n"
        "d.save('/workspace/outputs/out.docx')\n"
        "print('docx ok')"
    )
    docx_ok = r.ok and "docx ok" in r.stdout and any(
        f["name"] == "out.docx" and (f["size"] or 0) > 0 for f in r.output_files
    )
    check("生成 Word 文档(.docx)", docx_ok, r.to_tool_text())

    # 6) 断网护栏：沙箱内联网应失败
    r = await run_code(
        "import urllib.request; urllib.request.urlopen('http://example.com', timeout=3); print('LEAK')"
    )
    check("默认断网(联网失败, §7.1)", (not r.ok) and "LEAK" not in r.stdout, r.to_tool_text())

    # 7) 超时护栏
    r = await run_code("import time; time.sleep(999)", timeout_ms=3000)
    check("超时被砍(exit 124)", r.exit_code == 124, r.to_tool_text())

    ok = sum(results)
    print(f"\n{ok}/{len(results)} 通过")
    return ok == len(results)


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
