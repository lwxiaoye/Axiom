"""Platform overlay for the pinned upstream-first PPT authoring skill.

Keep authoring instructions in the vendored SKILL.md.  This module maps the
upstream local-agent assumptions onto the server sandbox and publication
boundary, and states the agent-executable export/publish loop.  It must not
prescribe a visual style or a fixed page generator.
"""
from __future__ import annotations

from typing import Any, Mapping


PROFILE_ID = "artifact_coding"
ARTIFACT_KIND = "presentation"
AUTHORING_BACKEND = "pptd"
STAGING_ROOT = "/workspace/tmp/ppt-project"
UPSTREAM_COMMIT = "07eeaadcb04c32c9adb107eb5c8608e6be4e1008"


def is_agentic_ppt_profile(profile: Any) -> bool:
    if hasattr(profile, "model_dump"):
        profile = profile.model_dump(mode="python")
    if not isinstance(profile, Mapping):
        return False
    return bool(
        str(profile.get("id") or profile.get("profile_id") or "") == PROFILE_ID
        and str(profile.get("artifact_kind") or "") == ARTIFACT_KIND
        and str(profile.get("authoring_backend") or "") == AUTHORING_BACKEND
    )


def platform_overlay() -> str:
    return f"""
平台执行规范（优先级高于技能中面向本机 CLI 的 Chromium / npx / serve 步骤。
逐步执行；每一步都必须是沙箱里现有工具能完成的动作，禁止空转探测）：

固定路径（禁止 ls/find 猜目录）：
- 技能目录 = 注入块「沙箱精确目录」，形如 `/workspace/skills/<精确目录>/`（只读工具箱：导出脚本、配方、reference）
- 工程目录 = {STAGING_ROOT}（可编辑的 PPTD 项目；会话工作区每轮 Pull 进来）
- 用户照片 = `{STAGING_ROOT}/media/`（工作区抽屉或输入框上传都会映射到这里，不要去 /workspace/files/ 找）
- 导出 PPTX：`python3 <技能目录>/scripts/run_export.py {STAGING_ROOT} --output {STAGING_ROOT}/deck.pptx --force`
  （wrapper 转发到同包 `scripts/export_pptx.py`；禁止 `--browser`、npx、npm、Chromium）
- 可选页图：`python3 <技能目录>/scripts/export_images.py {STAGING_ROOT} --output {STAGING_ROOT}/.qa-images --force`
  （沙箱走 LibreOffice/Poppler，不需要 npm 或浏览器）
- 交付：调用 `publish_ppt_artifact`（project_dir、pptx_path、filename）。这是唯一进「我的文件」的入口。
  PPTD 工程只保留在沙箱内用于编辑和质检，最终只向
  用户交付 .pptx，不要压缩、发布或在最终回答中列出 source ZIP。

逐步循环（缺工具就按跳过规则继续，不要停工重做）：
1. 有 `update_plan` 时只用 3–4 步对齐本循环（设计+写页 / 导出 / 发布）；没有该工具就跳过，继续写页。计划有效时禁止为了重复用户关键词而整表改标题。
2. 只读本题材需要的 1–3 个 reference。SKILL.md 已注入则禁止重读，也禁止再 `use_skill`。
3. 需要真实照片才 `search_web` + `fetch_ppt_asset`。搜索不可用或 requires:investigate 时跳过配图继续写页。
4. 用 `read_file` / `edit_file` / `write_file`（或 bash）在 {STAGING_ROOT} 写 DESIGN.md、.pptd、pages/。没有 apply_patch。不要把技能目录当工程。用户放入工作区的照片在 media/。
   不得改用 spec.json、create_deck.py 或固定版式生成器。禁止为了绕过
   路径或护栏自写 python-pptx/PptxGenJS 转换器。
5. 立刻跑上面的 `run_export.py`。失败才停并报告用户可见影响。不要等视觉审查通过再导出。
   禁止 `find /` 或全盘搜 `.wasm`；引擎由平台写入技能目录 `scripts/local-export/pptd_wasm_bg.wasm`。
6. 页图自检默认跳过；只有当前模型能看图且确有视觉疑点时才运行。失败或读不了图直接跳过，不得停工重做整份稿。
7. 立刻 `publish_ppt_artifact`。它只检查 ZIP/页数/布局等客观错误：禁止在发布前另跑 unzip、python-pptx/XML 坐标抽查、重复字体探测或第二套布局审计。只有确定性检查失败才改页、再导出、再发布；不做审美评分，也不调用独立视觉模型。

页数：用户指定优先；否则主题稿自定 6–10 页，有大纲则跟大纲。禁止为页数向用户确认后干等。
当前由服务器隔离沙箱执行。Node、Python、导出器和 WASM 已由平台预检并挂载；不要再次
探测运行时、依赖版本、PATH 或包状态。不要启动 npx serve，不要访问 kimi.com。
export_images.py 在平台沙箱内走 LibreOffice/Poppler 离线渲染，不需要 npm 或浏览器服务。

PPTD 必须使用 reference/pptd.md 的 v2 原生字段：manifest 写 `version: v2`、显式画布
`size: [width, height]`（与 DESIGN.md 及所有页面坐标一致，例如 1280×720 就写 `[1280, 720]`），
pages 为相对路径字符串。不得省略 size 或依赖导出器默认尺寸，避免右侧和底部被裁切。
每个元素写 `elementId`、`elementType`、`bounds`。`bounds` 固定写
`[x, y, width, height]`；矩形用 `elementType: shape` + `shapeName: rect`；图片 `fit` 写
`{{mode: cover}}` 或 `{{mode: contain}}`；分隔线宽高都至少 1px。文本的 `content`
必须是含 `text` 的对象，不能写成 `type/x/y/w/h` 或 content 数组的另一套 DSL。
富文本中用作比较符的字面 `<` 必须转义为 `&lt;`（如 `600 → &lt;100`）；
否则导出器可能把后续数值当标签吞掉。文本框尺寸必须按最终继承的 `lineHeight` /
`lineHeightPx`、`letterSpacing`、富文本字号、`wrap` 和 `align` 计算；单行 KPI/标题
显式设 `wrap: false` 并预留宽高，不得依赖导出器自动缩字。
标签相撞、换行进入下一元素、KPI 单位截断、文本框低于实际行高都是硬错；
必须改几何或字号后重新导出，不能用色块遮盖。平台发布前会再做确定性排版检查。
按 SKILL.md 读取当前题材需要的 reference 文件并逐页设计；不要默认暗色、黑金、照片铺底
或卡片墙，也不要为了统一而重复同一种版式。视觉方向由内容、用户参考和题材共同决定。
`search_web` 返回的 [图N] 只是候选目录，**不是已经下载的文件**。人物、产品、地点、建筑、
赛事/事件等具体题材先批量搜图，再用 `fetch_ppt_asset(url="图N", filename="语义名")`
把选中的图片放进本工程 media/，并在 .page 中以 `elementType: image` + `src: media/...`
明确引用。用户要求真实/比赛/现场照片时，插画、火柴人、海报和 Bash 自制 JPEG 都不是
照片替代品；每张合格照片必须保留 fetch_ppt_asset 的来源与字节校验记录。只搜索、只下载、
或只把文件留在 media/ 都不算使用素材，发布门禁会拒绝。
必须在 manifest 的 `theme.textStyles` 中显式声明至少两个字体角色（如 display/title 与 body），
每个角色写精确 `fontFamily`；禁止整份文稿依赖导出器默认字体。平台镜像固定提供
`Noto Sans CJK SC` 与 `Noto Serif CJK SC`，默认直接使用这两个精确名称，不要重复运行
`fc-list : family`。中文正文优先可读字体，标题通过另一字体角色、
字重、字距和字号建立性格；不要让所有标题、正文、数字都落到同一套默认 MiSans。
当前平台发布链使用本地 WASM，不能把“声明了字体”等同于“已嵌入字体”。最终渲染必须在
同一沙箱复核；若需要跨设备零替换，优先选择收件方常见字体，且不得声称已嵌入，除非发布后
实际检查 PPTX 的 `ppt/fonts/*.fntdata` 与 embeddedFontLst 均存在。
如果当前主模型不能读取图片，严格执行 SKILL.md 的结构审查降级并明确记录；
不得假装看过渲染图。平台不执行审美评分，用户若不满意可在后续对话中继续迭代。
""".strip()
