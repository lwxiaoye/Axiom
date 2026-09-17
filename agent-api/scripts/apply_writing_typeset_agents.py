"""把「写作助手」「文档排版」「树洞（演示）」三个对话 Agent 的配置（模型、提示词、工具、变量、
推荐场景、知识库、文件开关）写进工作台草稿，作为招标功能表的配置事实源。

招标功能表 → 入口映射（都是 chatAgent，运行时复用主智能体的工具循环；每个功能行对应运行页
右栏的一组「推荐场景」入口）：
  写作助手：① 场景素材组织 → ② 提纲与目录生成 → ③ 按提纲生成正文（局部修改与人工编辑：不做）
  文档排版：① 场景模板选择 → ② 模板排版与格式调整 → ③ 多格式导入与导出
  树洞：仅「支持性对话」（虚构场景、明确为演示应用）；情绪倾向识别 / 倾诉记录保护 / 严重问题预警
        与上报三行暂缓，提示词里明确不做。
模板 / PDF / 转换能力由系统工具 builtin.document_typeset / builtin.document_convert /
builtin.document_export 提供。

用法（agent-api/ 下，venv 内）：
  python scripts/apply_writing_typeset_agents.py --dry-run
  python scripts/apply_writing_typeset_agents.py --apply
  python scripts/apply_writing_typeset_agents.py --apply --knowledge-id <院系工作总结知识库ID>
  python scripts/apply_writing_typeset_agents.py --apply --only treehole

写作助手 / 文档排版按固定 app_id 更新草稿；树洞按「名称 + 归属人」查找，不存在则创建
（与 /workflow/app/add、/definition/save 相同的落库与审计路径）。只改草稿，不发布。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, text  # noqa: E402

from app.core.auth import UserContext  # noqa: E402
from app.core.database import async_session, engine  # noqa: E402
from app.models import WorkflowApp, WorkflowDefinition  # noqa: E402
from app.services.workflows.admin_governance_service import record_app_audit  # noqa: E402

OWNER = UserContext(user_id="2085197534705397761", username="wushaoran", tenant_id="0")

WRITING_APP_ID = "6e674621903542ee8508693bcab5ff0d"
TYPESET_APP_ID = "027427fd7e1741818f148bc35ad1caa8"

WRITING_MODEL = "deepseek-v4-pro"
TYPESET_MODEL = "deepseek-v4-flash"
TREEHOLE_MODEL = "deepseek-v4-pro"

# ---------------------------------------------------------------------------
# 写作助手
# ---------------------------------------------------------------------------

WRITING_PROMPT = """你是「写作助手」，为院系和部门做场景化写作：院系工作总结、科研报告、分析报告、公文。你只做三件事，并按顺序推进，不擅自跳步：
① 场景素材组织 → ② 提纲与目录生成 → ③ 按提纲生成正文并导出。
你不是段落级编辑器：不做"改第三段""润色这一句"这类局部改写。用户要局部修改时，说明本助手不提供该功能，建议下载 Word 后手工编辑，或把修改要求并入提纲重新生成。

【本次写作场景】{{scene}}
（为"由助手判断"时先从对话判断；判断不了就问一句，不要猜。）

## ① 场景素材组织（输入：素材 + 写作场景 → 输出：本次可用材料清单）
1. 材料来源与优先级：用户本轮上传或粘贴的材料 > 对话中的要点 > 知识库检索。绑定了知识库时，先调用 dataset_search，按场景关键词检索（如"工作总结 写作规范""院系 年度总结 素材清单""2025 教学 成效"），把检索到的规范、结构和往年素材纳入材料。
2. 输出「材料清单」：按主题分组列出可用的事实、数据、时间、人物、成果，每条标注出处（文件名 / 知识库片段 / 用户口述）。
3. 该文种通常需要、但材料里没有的内容，列成「待补充」清单向用户索要。禁止编造数据、名称、文号、日期、会议决议。
4. 结尾问一句：材料是否齐全、是否进入提纲阶段。

## ② 提纲与目录生成（输入：主题 + 材料 + 要求 → 输出：提纲 / 目录）
1. 依据本场景的默认结构（见下）和材料清单，输出带编号的层级提纲（一、/（一）/1.），每条后面用一句话说明拟写内容和引用的材料编号。
2. 本阶段不写正文。
3. 明确告诉用户可以"改第 X 条""合并 / 删除 / 新增"，或回复"确认提纲"。用户提出修改后，重新输出完整的新版提纲再请确认。

## ③ 按提纲生成正文（输入：已确认提纲 + 材料 → 输出：正文 + Word 文件）
1. 只有用户明确确认提纲（"确认""可以写""按这个写"等）后才写正文。用户一开始就说"直接成稿"时，可以把①②压缩：先用三到五行列出材料来源与提纲，再写正文。
2. 正文严格对应提纲：不新增提纲之外的章节，不删主要事实；事实、数据、时间只能来自材料清单，写不出的地方用「【待核实：xxx】」占位，不臆造。
3. 用 Markdown 组织：# 一级标题、## 二级标题、### 三级标题（标题文字前不要再写"一、""（一）"，导出时会自动编号），正文分段，数据对比用管道表格。
4. 写完后必须调用「导出到我的文件」：format=docx；template 按场景选（院系工作总结 / 公文 → "工作总结"，科研报告 / 分析报告 → "正式报告"）；title=文档标题；organization / date 有则填；content=完整正文（不含标题行）。用户要 PDF 时再调用一次 format=pdf。不要只在对话里贴一篇"伪 Word"。
5. 回复中简述：材料使用情况、待核实项、文件已存入"我的文件"（点卡片可预览）。需要换模板或转成 PDF 时，可引导使用「文档排版」智能体。

## 各场景默认结构
- 院系工作总结：工作回顾（党建与思想政治、教学、科研、学生工作、队伍建设、社会服务等，按材料取舍）→ 主要成效与亮点 → 存在的问题与不足 → 下阶段工作安排。
- 科研报告：研究背景与问题 → 研究方法 → 研究结果 → 结论与建议。
- 分析报告：背景与目的 → 数据与方法 → 分析发现 → 结论与建议。
- 公文：先确认文种和主送单位。通知：缘由 → 事项 → 要求；请示：缘由 → 事项 → 请求（一文一事，结尾"妥否，请批示"）；报告：情况 → 做法 → 问题 → 建议；函：事由 → 事项 → 结语。

## 表达要求
中文书面语，客观、简洁，多用材料中的数据和事实，少用空话套话；不出现"作为 AI"等自述；不用表情符号；每次回复开头用一行标明当前阶段（如「阶段②：提纲」）。"""

WRITING_WELCOME = (
    "我是写作助手，按「① 整理材料 → ② 生成提纲 → ③ 确认后写正文并导出 Word/PDF」三步工作。"
    "请告诉我写作场景（院系工作总结 / 科研报告 / 分析报告 / 公文），上传或粘贴材料；"
    "我会先给出材料清单和提纲，你确认后再写正文。局部段落修改不在本助手范围内。"
)

WRITING_VARIABLES = [
    {
        "id": "scene",
        "key": "scene",
        "label": "写作场景",
        "type": "select",
        "required": False,
        "valueType": "string",
        "description": "对应招标场景列；选「由助手判断」时由对话判断。",
        "defaultValue": "由助手判断",
        "enums": [
            {"label": "由助手判断", "value": "由助手判断"},
            {"label": "院系工作总结", "value": "院系工作总结"},
            {"label": "科研报告", "value": "科研报告"},
            {"label": "分析报告", "value": "分析报告"},
            {"label": "公文", "value": "公文"},
        ],
    }
]

WRITING_SCENES = [
    {
        "key": "material",
        "label": "① 场景素材组织",
        "textList": [
            "写作场景是院系工作总结。请先整理本次可用材料：我已上传素材，列出材料清单（含出处）和待补充项，先不要写提纲。",
            "请检索知识库里院系工作总结的写作规范和素材清单，整理成本次可用的材料清单。",
        ],
    },
    {
        "key": "outline",
        "label": "② 提纲与目录生成",
        "textList": [
            "请根据整理好的材料生成院系工作总结的提纲，逐条说明拟写内容和所用材料，我确认后再写正文。",
            "主题：XX学院2025年度科研工作分析。请根据我提供的材料和要求生成分析报告的目录结构。",
        ],
    },
    {
        "key": "draft",
        "label": "③ 按提纲生成正文",
        "textList": [
            "确认提纲。请按提纲和材料生成正文，保持事实与材料一致，写完导出 Word（工作总结模板）。",
            "直接成稿：请根据我上传的材料写一份科研报告，先列出提纲再写正文，最后导出 Word 和 PDF。",
        ],
    },
]

WRITING_TOOLS = [
    {"id": "builtin.document_export", "name": "导出到我的文件", "kind": "system"},
]

WRITING_DESCRIPTION = (
    "面向院系和部门的场景化写作：① 场景素材组织（复用知识库与上传材料）→ ② 提纲与目录生成（用户调整后确认）"
    "→ ③ 按提纲生成正文并导出 Word/PDF；覆盖工作总结、科研报告、分析报告、公文。不做段落级局部编辑。"
)

# ---------------------------------------------------------------------------
# 文档排版
# ---------------------------------------------------------------------------

TYPESET_PROMPT = """你是「文档排版」助手。你不做自由写作，只把已有正文变成排版规范、可直接使用的文档。三个功能对应三类请求，每次先判断用户要哪一类：

【用户预选模板】{{template}}
【用户预选输出格式】{{output_format}}

## 功能一：场景模板选择（输入：文稿类型 → 输出：可选模板）
- 平台内置两套可用模板，不需要用户上传：
  - 「工作总结」（公文式）：标题二号宋体加粗居中；一级标题黑体三号"一、"，二级楷体三号"（一）"，三级仿宋三号加粗"1."；正文仿宋_GB2312 三号、首行缩进两字符、固定行距 28 磅；表格带边框；页码"— 1 —"居中；文末单位、日期靠右落款。
  - 「正式报告」（报告式）：标题二号黑体加粗居中，单位、日期居中列于标题下；各级标题黑体 1 / 1.1 / 1.1.1；正文宋体小四、首行缩进两字符、1.5 倍行距；表格带边框、表头加粗底纹；页码居中。
- 用户只说文稿类型时，按下表推荐并说明理由：工作总结 / 年度总结 / 述职 / 通知请示类公文 → 「工作总结」；分析报告 / 调研报告 / 科研报告 / 评估报告 / 方案 → 「正式报告」。用户预选了模板就直接用。
- 用户自带 Word 模板（含 {{字段}} 占位符或"字段名 + 空白单元格"表格）时，走「填充 Word/Excel 模板」工具：先把将要填入的字段列给用户核对，再填充。
- 红头文件、文号、版心等复杂公文版式暂不支持，直说。

## 功能二：模板排版与格式调整（输入：正文 + 模板 → 输出：排版后的文档）
1. 正文来源：用户粘贴的文本；本轮上传的 DOCX / DOC / MD / TXT（用消息里标出的文件 ID 作为 source_file_id）；或上一轮对话里的正文。
2. 排版前先用三四行复述：将采用的模板、标题（title）、单位（organization）、日期（date）、识别到的标题层级（如"一级 4 条、二级 6 条"）和表格数量。标题层级识别不出时按"一、/（一）/1."惯例判断，拿不准就问一句；不要擅自改写正文内容。
3. 调用「套用排版模板」工具：template、content 或 source_file_id、title、organization、date、output_format（docx / pdf / docx+pdf）。工具会处理标题层级、字体字号、段落间距与首行缩进、基础表格样式和页码，并返回识别出的大纲。
4. 交付时说明：文件已存入"我的文件"，点文件卡片即可预览排版效果；把工具返回的大纲简要列出供核对。用户想调整（换模板、要 PDF、改标题/单位/日期）时重新调用工具生成；模板内的字号行距是固定规格，需要微调请下载 Word 后修改。

## 功能三：多格式导入与导出（输入：源文件 + 目标格式 → 输出：转换后的文件）
- 支持：DOCX / DOC → PDF（保留原排版）、DOCX / DOC → DOCX、DOCX / DOC → Markdown；MD / TXT → DOCX / PDF。调用「文档格式转换」工具，source_file_id 用用户消息里标出的文件 ID，target_format 用 pdf / docx / md。
- 不支持的格式（PPT、图片、扫描 PDF 等）直接说明，不假装转换。

## 规则
- 禁止用 Markdown / HTML 冒充排版结果，交付必须是真实文件。
- 不编造正文；正文缺失就请用户提供，或引导去「写作助手」先成稿。
- 不做段落级内容改写；用户要改内容时说明本助手只负责版式。
- 中文书面语，不用表情符号；每次回复开头一行标明当前功能（如「功能二：模板排版」）。"""

TYPESET_WELCOME = (
    "我是文档排版助手，负责三件事：① 推荐模板（内置「工作总结」「正式报告」两套）；"
    "② 把你的正文套模板排版，导出 Word / PDF；③ 文档格式转换（Word ⇄ PDF / Markdown）。"
    "请粘贴正文或上传文件，并告诉我要用的模板和输出格式。"
)

TYPESET_VARIABLES = [
    {
        "id": "template",
        "key": "template",
        "label": "模板",
        "type": "select",
        "required": False,
        "valueType": "string",
        "description": "平台内置两套排版模板；选「由助手推荐」时按文稿类型推荐。",
        "defaultValue": "由助手推荐",
        "enums": [
            {"label": "由助手推荐", "value": "由助手推荐"},
            {"label": "工作总结", "value": "工作总结"},
            {"label": "正式报告", "value": "正式报告"},
        ],
    },
    {
        "id": "output_format",
        "key": "output_format",
        "label": "输出格式",
        "type": "select",
        "required": False,
        "valueType": "string",
        "defaultValue": "docx",
        "enums": [
            {"label": "Word（docx）", "value": "docx"},
            {"label": "PDF", "value": "pdf"},
            {"label": "Word + PDF", "value": "docx+pdf"},
        ],
    },
]

TYPESET_SCENES = [
    {
        "key": "template",
        "label": "① 场景模板选择",
        "textList": [
            "我要排一份院系年度工作总结，应该用哪套模板？请说明两套内置模板的区别。",
            "这是一份调研分析报告，请推荐合适的模板并说明版式规格。",
        ],
    },
    {
        "key": "typeset",
        "label": "② 模板排版与格式调整",
        "textList": [
            "请用「工作总结」模板排版我上传的正文，单位是 XX学院，日期 2026年1月，导出 Word。",
            "请用「正式报告」模板排版下面的正文（含表格），同时导出 Word 和 PDF。",
        ],
    },
    {
        "key": "convert",
        "label": "③ 多格式导入与导出",
        "textList": [
            "请把我上传的 Word 文档原样转成 PDF。",
            "请把我上传的 Word 文档转成 Markdown 文本，方便我继续编辑。",
        ],
    },
]

TYPESET_TOOLS = [
    {"id": "builtin.document_typeset", "name": "套用排版模板", "kind": "system"},
    {"id": "builtin.document_convert", "name": "文档格式转换", "kind": "system"},
    {"id": "builtin.template_fill", "name": "填充 Word/Excel 模板", "kind": "system"},
]

TYPESET_DESCRIPTION = (
    "把已有正文变成规范文档：① 场景模板选择（内置「工作总结」「正式报告」）→ ② 模板排版与格式调整"
    "（标题层级、正文字体、段落间距、基础表格、页码，文件卡片可预览）→ ③ 多格式导入与导出"
    "（DOCX/DOC/MD/TXT 输入，DOCX/PDF/MD 输出）。不做自由创作。"
)

# ---------------------------------------------------------------------------
# 树洞（演示）——只做「支持性对话」；情绪识别 / 记录保护 / 预警上报暂缓
# ---------------------------------------------------------------------------

TREEHOLE_NAME = "树洞（演示）"

TREEHOLE_PROMPT = """你是「树洞」，一个面向学生的支持性对话演示应用。你的工作是倾听、共情、安抚，并给出温和、可行的引导。

## 定位与边界（必须遵守）
1. 这是演示应用：用虚构的校园场景演示支持性对话，不提供真实的心理咨询、危机干预或投诉受理服务，也不代表学校任何部门作出承诺。首轮回复中自然地提醒一次，之后不必反复强调。
2. 不收集、不追问真实个人信息：不问真实姓名、学号、班级、联系方式；对方主动说出时，提醒"演示中不必提供真实身份信息"，然后继续以对方自称的称呼交流。
3. 情绪倾向识别与记录、倾诉记录加密保护、严重问题预警与上报，都不在本演示范围内：不给对方贴情绪标签，不说"我已记录/已上报"，不承诺"替你反映给学校"；对方问到时如实说明这些功能尚未开放。
4. 不提供医学诊断、用药建议、法律结论；不替对方做重大决定；不评判、不说教，不用"你应该"式的命令语气。
5. 对方要求闲聊、写作业、查资料等与倾诉无关的事，礼貌说明本应用只做支持性对话，可以去主对话或其他智能体。

## 对话方式
- 先接住情绪，再理解事实，最后才给建议：用一两句话反映对方的感受（"听起来你这几天一直绷着"），确认自己理解得对不对，再问一个开放式问题让对方多说；不要一上来列一堆办法。
- 每次回复 3～6 句，像面对面聊天，不用标题和长列表；一次只问一个问题。
- 引导方向：帮对方把问题说清楚、看见自己已经做到的部分、找到一个眼下能做的小步骤（休息、和信任的人说说、去找辅导员 / 任课老师 / 学校心理咨询中心）。
- 不否定感受，不轻易说"没事的""想开点"；也不放大问题、不替对方下结论。
- 用对方的语言和称呼；对方沉默或只发一个字时，温和地陪着，不催。

## 安全处理（优先级最高）
对方提到伤害自己、结束生命、伤害他人、正在遭受暴力或处于紧急危险时：
1. 立刻放下其他话题，直接、平静地表达关心，确认对方此刻是否安全、身边有没有人。
2. 明确说明：这是演示应用，无法提供紧急帮助；请立即联系身边可信任的人、学校心理咨询中心或辅导员；紧急情况拨打 110 / 120；全国心理援助热线 12356（24 小时）。
3. 不评判、不劝"想开点"、不追问细节、不提供任何可能造成伤害的方法信息。
4. 在对方确认安全之前，不切换回普通话题。

## 演示场景（虚构，用于展示对话效果）
考试周焦虑、和室友闹矛盾、被导师批评后自我怀疑、家里催就业、失恋后提不起劲、竞选失利、异地父母生病自己回不去。对方选了某个场景时，把它当作虚构角色的处境来回应，不要求补充真实细节。

## 语气
温和、平实、有耐心；用"我"和"你"，少用"我们建议"；避免专业术语和空泛的鸡汤；不用表情符号（对方用了可以少量呼应）。"""

TREEHOLE_WELCOME = (
    "这里是「树洞」演示版：用虚构的校园场景演示支持性对话，不是真实的心理咨询或倾诉受理渠道。"
    "对话按平台普通会话保存，尚未启用专门的加密与访问保护，请不要输入真实姓名、学号或涉及隐私的真实经历。"
    "可以从右侧选一个演示场景，或直接说说想聊的事。"
    "如果你或身边的人正处在危险中，请立即联系身边的人或学校心理咨询中心，紧急情况拨打 110 / 120，全国心理援助热线 12356。"
)

TREEHOLE_SCENES = [
    {
        "key": "demo",
        "label": "演示场景（虚构）",
        "textList": [
            "考试周要到了，我复习不进去，越急越看不进书，晚上也睡不好。",
            "我和室友因为作息问题吵了一架，现在宿舍气氛很僵，我不知道该怎么办。",
            "导师今天当着大家的面说我做的东西没用，我开始怀疑自己是不是不适合做研究。",
            "家里天天催我找工作，可我投了几十份简历都没回音，感觉自己很没用。",
            "分手一个月了，还是提不起劲，上课都在走神。",
            "爸妈在老家住院了，我在外地回不去，一边上课一边担心，很无力。",
        ],
    },
    {
        "key": "boundary",
        "label": "边界演示",
        "textList": [
            "你能把我说的这些记下来、帮我反映给辅导员吗？",
            "顺便帮我写一段这周的实习周报吧。",
        ],
    },
]

TREEHOLE_DESCRIPTION = (
    "支持性对话演示应用：以虚构校园场景演示倾听、共情、安抚与引导，明确为演示、不开放真实倾诉业务。"
    "情绪倾向识别、倾诉记录保护、严重问题预警与上报三项暂缓，不在本应用内实现。"
)


def _preset(
    *,
    key,
    app_id,
    name,
    model,
    prompt,
    welcome,
    variables,
    scenes,
    tools,
    description,
    history,
    app_category,
    files,
):
    return {
        "key": key,
        "app_id": app_id,
        "name": name,
        "model": model,
        "systemPrompt": prompt,
        "welcomeText": welcome,
        "variables": variables,
        "scenes": scenes,
        "tools": tools,
        "description": description,
        "history": history,
        "app_category": app_category,
        "files": files,          # 是否允许上传文件 / 抽取文件内容 / 识图
    }


PRESETS = {
    "writing": _preset(
        key="writing", app_id=WRITING_APP_ID, name="写作助手", model=WRITING_MODEL,
        prompt=WRITING_PROMPT, welcome=WRITING_WELCOME, variables=WRITING_VARIABLES,
        scenes=WRITING_SCENES, tools=WRITING_TOOLS, description=WRITING_DESCRIPTION,
        history=12, app_category="content_creation", files=True,
    ),
    "typeset": _preset(
        key="typeset", app_id=TYPESET_APP_ID, name="文档排版", model=TYPESET_MODEL,
        prompt=TYPESET_PROMPT, welcome=TYPESET_WELCOME, variables=TYPESET_VARIABLES,
        scenes=TYPESET_SCENES, tools=TYPESET_TOOLS, description=TYPESET_DESCRIPTION,
        history=8, app_category="content_creation", files=True,
    ),
    "treehole": _preset(
        key="treehole", app_id=None, name=TREEHOLE_NAME, model=TREEHOLE_MODEL,
        prompt=TREEHOLE_PROMPT, welcome=TREEHOLE_WELCOME, variables=[],
        scenes=TREEHOLE_SCENES, tools=[], description=TREEHOLE_DESCRIPTION,
        history=20, app_category="communication", files=False,
    ),
}


# ---------------------------------------------------------------------------
# 草稿图：与工作台「对话 Agent」表单编译出的三节点结构一致（系统配置 → 流程开始 → 对话 Agent）
# ---------------------------------------------------------------------------

AGENT_NODE_ID = "7BdojPlukIQw"


def _hidden(key: str, value_type: str, value=None) -> dict:
    item = {"key": key, "label": "", "renderTypeList": ["hidden"], "selectedTypeIndex": 0, "valueType": value_type}
    if value is not None:
        item["value"] = value
    return item


def base_agent_graph() -> dict:
    return {
        "version": "2.1.0",
        "fastgpt": {
            "nodes": [
                {"nodeId": "userGuide", "flowNodeType": "userGuide", "name": "系统配置", "intro": "",
                 "position": {"x": 531.24, "y": -486.76}, "inputs": [], "outputs": []},
                {"nodeId": "workflowStartNodeId", "flowNodeType": "workflowStart", "name": "流程开始",
                 "intro": "工作流入口，接收用户问题与全局变量", "position": {"x": 558.4, "y": 123.72},
                 "inputs": [{"key": "userChatInput", "renderTypeList": ["reference", "textarea"], "valueType": "string",
                             "label": "用户问题", "required": True, "toolDescription": "用户问题"}],
                 "outputs": [{"id": "userChatInput", "key": "userChatInput", "label": "用户问题", "type": "static",
                              "valueType": "string"}]},
                {"nodeId": AGENT_NODE_ID, "flowNodeType": "agent", "name": "对话 Agent",
                 "intro": "模型自主决定调用工具、技能与知识库，产出最终回答。", "position": {"x": 1106.32, "y": -350.6},
                 "inputs": [
                     {"key": "model", "label": "模型", "renderTypeList": ["settingLLMModel", "reference"],
                      "selectedTypeIndex": 0, "valueType": "string", "value": ""},
                     {"key": "systemPrompt", "label": "提示词", "renderTypeList": ["textarea", "reference"],
                      "selectedTypeIndex": 0, "valueType": "string", "maxLength": 100000,
                      "description": "模型固定的引导词，可使用变量，通过调整该内容引导模型聊天方向",
                      "placeholder": "例如：你是一个严谨的校园业务助手。", "value": ""},
                     _hidden("temperature", "number"),
                     _hidden("maxToken", "number"),
                     _hidden("aiChatVision", "boolean", True),
                     _hidden("aiChatAudio", "boolean", False),
                     _hidden("aiChatVideo", "boolean", False),
                     _hidden("aiChatExtractFiles", "boolean", True),
                     _hidden("aiChatReasoning", "boolean", True),
                     _hidden("aiChatReasoningEffort", "string"),
                     _hidden("aiChatTopP", "number"),
                     _hidden("aiChatStopSign", "string"),
                     _hidden("aiChatResponseFormat", "string"),
                     _hidden("aiChatJsonSchema", "string"),
                     {"key": "history", "label": "上下文轮数", "renderTypeList": ["numberInput"], "selectedTypeIndex": 0,
                      "valueType": "number", "min": 0, "max": 30, "value": 6},
                     {"key": "fileUrlList", "label": "文件链接", "renderTypeList": ["reference", "input"],
                      "selectedTypeIndex": 0, "valueType": "arrayString", "value": ["workflowStartNodeId", "userFiles"]},
                     {"key": "userChatInput", "label": "用户问题", "renderTypeList": ["reference", "textarea"],
                      "selectedTypeIndex": 0, "valueType": "string", "required": True, "toolDescription": "用户问题",
                      "value": ["workflowStartNodeId", "userChatInput"]},
                     _hidden("isResponseAnswerText", "boolean", True),
                     _hidden("agent_selectedTools", "arrayObject", []),
                     _hidden("agent_datasetParams", "object", {
                         "datasets": [], "similarity": 0.4, "limit": 5000, "searchMode": "embedding",
                         "embeddingWeight": 0.5, "usingReRank": False,
                     }),
                     _hidden("skills", "arrayObject", []),
                 ],
                 "outputs": [
                     {"id": "answerText", "key": "answerText", "label": "AI 回复内容", "type": "static", "valueType": "string"},
                     {"id": "system_error_text", "key": "system_error_text", "label": "错误信息", "type": "error",
                      "valueType": "string"},
                 ]},
            ],
            "edges": [{"source": "workflowStartNodeId", "sourceHandle": "workflowStartNodeId-source-right",
                       "target": AGENT_NODE_ID, "targetHandle": f"{AGENT_NODE_ID}-target-left"}],
            "chatConfig": {"welcomeText": "", "variables": [],
                           "fileSelectConfig": {"canSelectFile": True, "canSelectImg": True, "maxFiles": 10},
                           "chatInputGuide": {"open": False, "textList": [], "sceneList": []}},
        },
    }


def _set_input(agent_node: dict, key: str, value) -> None:
    for item in agent_node.get("inputs") or []:
        if item.get("key") == key:
            item["value"] = value
            return
    raise KeyError(f"agent 节点缺少输入 {key}，请先在工作台保存一次草稿再运行本脚本")


def build_draft(draft: dict, preset: dict, knowledge: list[dict]) -> dict:
    graph = draft.get("fastgpt") or draft
    agent_node = next(
        (node for node in graph.get("nodes") or [] if node.get("flowNodeType") == "agent"), None
    )
    if agent_node is None:
        raise ValueError("草稿里没有 agent 节点")
    files = bool(preset["files"])
    _set_input(agent_node, "model", preset["model"])
    _set_input(agent_node, "systemPrompt", preset["systemPrompt"])
    _set_input(agent_node, "history", preset["history"])
    _set_input(agent_node, "aiChatExtractFiles", files)
    _set_input(agent_node, "aiChatVision", files)
    _set_input(agent_node, "agent_selectedTools", preset["tools"])
    _set_input(agent_node, "skills", [])
    dataset_params = next(
        (item.get("value") for item in agent_node.get("inputs") or [] if item.get("key") == "agent_datasetParams"),
        None,
    )
    dataset_params = dict(dataset_params or {})
    if knowledge:
        # 与工作台表单一致：{datasetId, name}，name 用于编辑器里的知识库标签
        dataset_params["datasets"] = [dict(item) for item in knowledge]
    dataset_params.setdefault("datasets", [])
    dataset_params.setdefault("similarity", 0.4)
    dataset_params.setdefault("limit", 5000)
    dataset_params.setdefault("searchMode", "embedding")
    dataset_params.setdefault("embeddingWeight", 0.5)
    dataset_params.setdefault("usingReRank", False)
    _set_input(agent_node, "agent_datasetParams", dataset_params)

    chat_config = graph.setdefault("chatConfig", {})
    chat_config["welcomeText"] = preset["welcomeText"]
    chat_config["variables"] = preset["variables"]
    chat_config["fileSelectConfig"] = {"canSelectFile": files, "canSelectImg": files, "maxFiles": 10}
    chat_config["chatInputGuide"] = {
        "open": True,
        "textList": [text for scene in preset["scenes"] for text in scene["textList"]],
        "sceneList": preset["scenes"],
    }
    return draft


async def _find_app(session, preset: dict) -> WorkflowApp | None:
    if preset["app_id"]:
        return (await session.execute(select(WorkflowApp).where(WorkflowApp.id == preset["app_id"]))).scalar_one_or_none()
    rows = (
        await session.execute(
            select(WorkflowApp).where(
                WorkflowApp.name == preset["name"],
                WorkflowApp.owner_user_id == OWNER.user_id,
                WorkflowApp.ai_app_type == "chatAgent",
            ).order_by(WorkflowApp.create_time.desc())
        )
    ).scalars().all()
    return rows[0] if rows else None


def _create_app(session, preset: dict) -> WorkflowApp:
    # 与 /workflow/app/add 同一落库与审计路径
    app = WorkflowApp(
        id=uuid.uuid4().hex,
        tenant_id=OWNER.tenant_id,
        ai_app_type="chatAgent",
        name=preset["name"],
        description=preset["description"],
        app_category=preset["app_category"],
        app_icon="",
        config_json="{}",
        status="draft",
        owner_user_id=OWNER.user_id,
        owner_username=OWNER.username,
    )
    session.add(app)
    record_app_audit(
        session, app=app, action="create", actor=OWNER,
        after={"name": app.name, "description": app.description, "appCategory": app.app_category,
               "appIcon": "", "status": app.status, "aiAppType": app.ai_app_type},
    )
    return app


async def _knowledge_refs(session, knowledge_ids: list[str]) -> list[dict]:
    """知识库由 Java 维护（ai_knowledge_base），这里只读名称给编辑器标签用；查不到就只带 ID。"""
    refs: list[dict] = []
    for kid in knowledge_ids:
        name = ""
        try:
            row = (await session.execute(text("SELECT name FROM ai_knowledge_base WHERE id = :id"), {"id": kid})).first()
            name = str(row[0]) if row else ""
        except Exception:  # noqa: BLE001
            name = ""
        refs.append({"datasetId": kid, "name": name or kid})
    return refs


async def run(apply: bool, knowledge_ids: list[str], only: list[str]) -> None:
    async with async_session() as session:
        knowledge = await _knowledge_refs(session, knowledge_ids)
        for key, preset in PRESETS.items():
            if only and key not in only:
                continue
            app = await _find_app(session, preset)
            created = False
            if app is None:
                if preset["app_id"]:
                    print(f"[{preset['name']}] 应用不存在：{preset['app_id']}，跳过")
                    continue
                if not apply:
                    print(f"[{preset['name']}] 不存在，--apply 时将创建（chatAgent，草稿，归属 {OWNER.username}）")
                    app = None
                else:
                    app = _create_app(session, preset)
                    created = True
            definition = None
            if app is not None:
                definition = (
                    await session.execute(select(WorkflowDefinition).where(WorkflowDefinition.app_id == app.id))
                ).scalar_one_or_none()
            draft = json.loads(definition.draft_json) if definition and definition.draft_json else base_agent_graph()
            bind = knowledge if key == "writing" else []
            new_draft = build_draft(draft, preset, bind)
            print(
                f"[{preset['name']}] id={app.id if app else '(新建)'} 状态={app.status if app else 'draft'} "
                f"模型={preset['model']} 工具={[t['id'] for t in preset['tools']] or '无'} 知识库={[f"{b['name']}({b['datasetId']})" for b in bind] or '（未绑定）'} "
                f"文件上传={'开' if preset['files'] else '关'} 场景={[s['label'] for s in preset['scenes']]}"
            )
            if not apply or app is None:
                continue
            workflow_json = json.dumps(new_draft, ensure_ascii=False)
            had_draft = bool(definition and definition.draft_json)
            if not definition:
                definition = WorkflowDefinition(id=uuid.uuid4().hex, app_id=app.id, published_version=0)
                session.add(definition)
            if definition.draft_json != workflow_json:
                # 与 /workflow/definition/save 同一审计动作
                record_app_audit(
                    session, app=app, action="save_draft", actor=OWNER,
                    before={"hasDraft": had_draft}, after={"hasDraft": True},
                )
            definition.draft_json = workflow_json
            definition.update_time = datetime.now()
            if app.description != preset["description"] and not created:
                record_app_audit(
                    session, app=app, action="update_app", actor=OWNER,
                    before={"description": app.description, "configChanged": False},
                    after={"description": preset["description"], "configChanged": False},
                )
            app.description = preset["description"]
            if not app.app_category:
                app.app_category = preset["app_category"]
            app.update_time = datetime.now()
        if apply:
            await session.commit()
            print("已写入草稿；到工作台「我的智能体」打开对应 Agent 可核对并提交发布。")
        else:
            print("dry-run：未写入。加 --apply 执行。")
    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="写入数据库（默认 dry-run）")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写入")
    parser.add_argument("--knowledge-id", action="append", default=[], help="绑定到写作助手的知识库 ID，可多次")
    parser.add_argument("--only", action="append", choices=list(PRESETS), default=[], help="只处理某个 Agent")
    parser.add_argument("--dump", help="把三份提示词/场景配置导出为 JSON 文件（不连库）")
    args = parser.parse_args()
    if args.dump:
        Path(args.dump).write_text(json.dumps(PRESETS, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已导出 {args.dump}")
        return
    asyncio.run(run(apply=args.apply and not args.dry_run, knowledge_ids=args.knowledge_id, only=args.only))


if __name__ == "__main__":
    main()
