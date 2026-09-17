# -*- coding: utf-8 -*-
"""交付物判据（2026-07-27 用户拍板）——什么算「交给用户的东西」，什么只是过程文件。

## 为什么要这条闸

统一文件系统落地后「写进 /workspace/files 就是已保存」，代价是模型的**过程文件也一起进了
用户的文件区**：做一份 PPT 时先写 `build.py` 生成脚本，用户就在对话里收到一张
`workspace_tmp_build.py` 的产物卡（PY · 15.1 KB · 已保存到我的文件），真正的 pptx 混在里面。
`workspace_sync._INTERMEDIATE_EXTS` 那条黑名单只管 bash 回写这一条路径，`write_file` /
`create_file` / `execute_in_sandbox` 三条路都绕过它。

用户拍板：**产物卡与「我的文件」都只展示文档类产物**（word / pdf / excel / ppt / md 之类，
任务模式的《任务计划.md》天然在内），其余（.py 脚本、.json 中间数据、日志……）照常落库供模型
下一步继续用，但不作为交付物露出。

## 白名单而不是黑名单

`workspace_sync` 当初选黑名单的理由是「交付格式是开放集合，白名单会静默吞掉没预料到的格式」。
用户 2026-07-27 明确反转了这个取舍：宁可漏掉少数冷门格式，也不要文件区被过程文件淹没。
**网页（.html/.htm）保留**——平台本来就能行内预览它，用户特意点名要留（"我们之前不也是有
html 可以展示吗"）。图片保留同理：execute_in_sandbox 那条路已经把「嵌进文档的中间图」滤掉了，能走到
这里的图片就是用户真要的那张。

## 两条轴：格式 + 来源

光看格式不够。真机第二例：用户只说「扫描一下这个 GitHub 仓库的代码」，模型用 `download_url`
逐个取回 16 个源文件，文件区一次多出 `main.tsx` / `package.json` / `docker-compose.yml` /
`README.md` / `requirements.txt`……**后两个是白名单里的格式**，只按后缀判还是会露出来。

所以 `download_url` 落地的文件单独标 `source="material"`（取来的原材料）：它是**输入**，
不是本轮的产出。代价是「帮我下载这个 PDF」时那份 PDF 也不出卡——用户仍能在「我的文件」
里打开「显示全部文件」看到它（清单默认藏、不删除，见 routers/files.py 的 `show_all`）。

## 「我的文件」页上传永远可见；对话框附件不是

`source == "uploaded"` 一律判可交付：用户在「我的文件」页主动上传的 .py/.zip 是
**他自己的文件**，平台没资格因为后缀不在白名单里就藏掉。

对话框 / composer 放下的图片、PPT、粘贴文本走 `source=workspace`，那是本轮素材，
不是产物，清单（含「显示全部文件」）不露出。成品 PPT 仍只经发布进「我的文件」。
"""
from __future__ import annotations

import os
from typing import Optional

#: `download_url` 取回的外部字节。既不是用户传的、也不是本轮产出的，属于「材料」。
MATERIAL_SOURCE = "material"
#: PPTD 工程跨 Run 检查点。不是交付物，也不进模型 list_files / 沙箱 files 镜像。
#: 必须 ≤16 字符：`agent_user_file.source` / `agent_user_file_version.source` 是 VARCHAR(16)。
#: 真机 `staging_checkpoint`（19）INSERT 报 Data too long，检查点打包成功却落不了库。
STAGING_CHECKPOINT_SOURCE = "staging_ckpt"
#: 工作区对象若误写入「我的文件」表，也不得当交付物露出。
WORKSPACE_SOURCE = "workspace"
_STAGING_CHECKPOINT_SOURCES = frozenset({
    STAGING_CHECKPOINT_SOURCE,
    "staging_checkpoint",  # 旧名：兼容未写入成功前的测试与注释
    WORKSPACE_SOURCE,
})
assert len(STAGING_CHECKPOINT_SOURCE) <= 16


def is_staging_checkpoint_source(source: Optional[str] = None) -> bool:
    return str(source or "") in _STAGING_CHECKPOINT_SOURCES

# 文档类：用户口径的「交付产物」。任务模式投影的《任务计划.md》落在 .md 上。
_DOC_EXTS = frozenset({
    ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".pdf",
    ".md", ".markdown", ".txt", ".csv", ".tsv",
})
# 网页：平台有行内预览 + 全屏查看器，是能直接交给用户看的东西（用户点名保留）
_WEB_EXTS = frozenset({".html", ".htm"})
# 图片：能走到交付层的图片已是最终产物（中间素材在 execute_in_sandbox harvest 阶段就被滤掉了）
_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg"})

DELIVERABLE_EXTS = _DOC_EXTS | _WEB_EXTS | _IMAGE_EXTS

#: 编辑器的内部伴生文件：**不是交付物，但清单里必须带着**。
#: `<名>.slides.json` 是幻灯片编辑源；`<名>.research.md` 是研究报告 Markdown 源。
_COMPANION_SUFFIXES = (".slides.json", ".research.md")
#: 办公批量交付打包名。其它 generated zip 仍不是产物卡（见 test_generated_ppt_source_bundle）。
_BATCH_DELIVERABLE_ARCHIVES = frozenset({"batch-deliverables.zip"})


def is_editor_companion(filename: str) -> bool:
    return str(filename or "").strip().lower().endswith(_COMPANION_SUFFIXES)


def is_deliverable(filename: str, source: Optional[str] = "generated") -> bool:
    """这个文件该不该作为交付产物露给用户（产物卡 / 「我的文件」清单 / 交付审查）。

    判据只有三条，故意不看内容也不看体积——判据要能一句话说清楚，否则用户下次看到
    某个文件没出现时无从解释。
    """
    if is_editor_companion(filename):
        return False
    src = str(source or "")
    if src == MATERIAL_SOURCE or is_staging_checkpoint_source(src):
        return False  # 材料与 PPTD 检查点都不是交给用户的产物
    if src not in ("generated", "research", ""):
        return True  # 上传件 / 恢复的历史版本：用户自己的东西，一律可见
    name = os.path.basename(str(filename or "").strip().lower())
    if name in _BATCH_DELIVERABLE_ARCHIVES:
        return True
    ext = os.path.splitext(str(filename or "").strip().lower())[1]
    return ext in DELIVERABLE_EXTS


def filter_rows(rows) -> list:
    """按行过滤落库结果 dict 列表（`{"filename": ..., "source": ...}` 形状）。

    行里若已带 `deliverable`（`_row_to_dict` 算好的）就直接用，避免同一判据算两遍、
    也让「历史行没有这个字段」时能按文件名兜底。
    """
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        flag = row.get("deliverable")
        if flag is None:
            flag = is_deliverable(
                str(row.get("filename") or row.get("name") or ""),
                row.get("source"),
            )
        if flag:
            out.append(row)
    return out
