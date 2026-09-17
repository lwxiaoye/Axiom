"""沙箱 LibreOffice 版式转换（DOCX/DOC/PPTX/XLSX → PDF、DOC → DOCX 等）的唯一入口。

文件预览（user_file_service.get_preview_pdf）与文档排版 / 格式转换系统工具共用这一条链路，
避免两处各拼一份 soffice 命令、字体替换规则不一致。

沙箱镜像只有 Noto CJK 字体：公文常用的仿宋 / 楷体 / 宋体 / 黑体在 fontconfig 里没有别名时会
一律回退成默认无衬线，PDF 里标题与正文没有区分。转换前写一份用户级 fonts.conf，把
宋 / 仿宋 / 楷 映射到 Noto Serif CJK、黑体 / 雅黑 映射到 Noto Sans CJK。
"""
from __future__ import annotations

DEFAULT_TIMEOUT_MS = 120_000

FONTCONFIG_ALIASES = (
    "<?xml version=\"1.0\"?>\n"
    "<!DOCTYPE fontconfig SYSTEM \"fonts.dtd\">\n"
    "<fontconfig>\n"
    + "".join(
        f"  <alias binding=\"strong\"><family>{family}</family>"
        f"<prefer><family>{target}</family></prefer></alias>\n"
        for family, target in (
            ("宋体", "Noto Serif CJK SC"),
            ("SimSun", "Noto Serif CJK SC"),
            ("仿宋", "Noto Serif CJK SC"),
            ("仿宋_GB2312", "Noto Serif CJK SC"),
            ("FangSong", "Noto Serif CJK SC"),
            ("楷体", "Noto Serif CJK SC"),
            ("楷体_GB2312", "Noto Serif CJK SC"),
            ("KaiTi", "Noto Serif CJK SC"),
            ("方正小标宋简体", "Noto Serif CJK SC"),
            ("黑体", "Noto Sans CJK SC"),
            ("SimHei", "Noto Sans CJK SC"),
            ("微软雅黑", "Noto Sans CJK SC"),
            ("Microsoft YaHei", "Noto Sans CJK SC"),
        )
    )
    + "</fontconfig>\n"
)


class OfficeConvertError(RuntimeError):
    """soffice 没有产出目标文件（含沙箱不可用）。"""


def build_convert_script(target_ext: str) -> str:
    target_ext = target_ext.lower().lstrip(".")
    return (
        "import glob, os, subprocess, sys\n"
        f"conf = {FONTCONFIG_ALIASES!r}\n"
        "cfg_dir = os.path.expanduser('~/.config/fontconfig')\n"
        "os.makedirs(cfg_dir, exist_ok=True)\n"
        "open(os.path.join(cfg_dir, 'fonts.conf'), 'w', encoding='utf-8').write(conf)\n"
        "src = glob.glob('/workspace/inputs/*')[0]\n"
        "r = subprocess.run(\n"
        f"    ['soffice', '--headless', '--norestore', '--convert-to', {target_ext!r},\n"
        "     '--outdir', '/workspace/outputs', src],\n"
        "    capture_output=True, text=True, timeout=100,\n"
        ")\n"
        "sys.stderr.write(r.stderr or '')\n"
        f"sys.exit(0 if glob.glob('/workspace/outputs/*.{target_ext}') else 1)\n"
    )


async def convert_with_soffice(
    filename: str, data: bytes, target_ext: str, *, timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> bytes:
    """把 filename/data 交给沙箱里的 LibreOffice 转成 target_ext，返回目标文件字节。"""
    from app.services.sandbox import sandbox_executor

    target_ext = target_ext.lower().lstrip(".")
    result = await sandbox_executor.execute_in_sandbox(
        build_convert_script(target_ext),
        input_files={filename: data},
        fetch_output_bytes=True,
        timeout_ms=timeout_ms,
    )
    outputs = [
        item for item in (result.output_files or [])
        if str(item.get("name") or "").lower().endswith(f".{target_ext}") and item.get("content")
    ]
    if not result.ok or not outputs:
        detail = (result.error or result.stderr or "")[:200]
        raise OfficeConvertError(
            f"exit={result.exit_code} {detail or '未产出文件'}"
        )
    return outputs[0]["content"]
