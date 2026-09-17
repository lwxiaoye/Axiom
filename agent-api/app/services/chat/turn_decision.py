"""主对话回合决策（主对话最终方案 V3：一个对话框、能力常驻）。

正常对话和任务模式都具备创建/修改文件的能力；任务模式只决定是否使用可恢复的计划图，
不再充当“写权限开关”。只有用户明确说“只分析/不要修改”时才临时剥离写工具。内部仍保留
inspect/mutate 标签供审计，但产品界面和模型回复不得把它描述成“只读模式”。

活动任务中针对当前产物的意见走 RunInstruction steering（前端运行中发送即引导），
不经过本函数。修改既有内容走 RevisionTarget 原位修改（哈希闸 + 禁止另建副本）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.services.agent_harness.plan_content import PLAN_CONTENT_CONTRACT


TurnIntent = Literal[
    "conversation",
    "feedback",
    "execute",
    "revise",
    "steer",
    "pause",
    "continue",
    "cancel",
    "replace",
]
ActionAuthority = Literal["none", "inspect", "mutate"]
TurnRoute = Literal["direct_answer", "agent", "planning", "steer", "queue", "cancel", "resume"]


_PAUSE_RE = re.compile(r"^(先)?(暂停|停一下|等一下|稍等|先别做|先停)")
_CONTINUE_RE = re.compile(r"^(继续|接着|恢复|接着做|继续执行)(吧|下去|就行|即可)?[。！! ]*$")
_CANCEL_RE = re.compile(r"^(取消|停止|终止|别做了|不用做了|结束任务)(吧|任务|执行)?[。！! ]*$")
_REPLACE_RE = re.compile(r"(停止|结束|取消).{0,8}(重新|重来|新任务)|(推翻|从头).{0,5}(重做|开始)")

_QUESTION_RE = re.compile(
    r"^(怎么|如何|为什么|能否|可不可以|是否|什么是|解释|分析一下|说说|讲讲)"
)
_FEEDBACK_RE = re.compile(
    r"(我觉得|感觉|看起来|似乎|好像|有点|不太|不够|太挤|太乱|不好用|不顺手|建议|意见|想法)"
)
_MUTATING_EFFECT_WORDS = (
    r"创建|新建|生成|制作|实现|开发|运行|跑|构建|测试|验证|修复|部署|安装|"
    r"修改|改成|改(?!进|善|革)|调整|优化|完善|更新|编辑|重写|重做|重新做|删除|合并|拆分|"
    r"转换|导出|保存|写入|写出|写到|写进|写个|写一|提交|发布|换|调大|调小|改大|改小|移到|加上|去掉"
)
# “直接执行”描述的是确认策略，不应把同一句里的“分析/检查”从只读任务升级成写权限。
# 当句子没有任何只读动作时，它仍代表执行授权（例如“直接执行部署”）。
# 「跑」与「执行」同属通用执行授权（2026-08-04 真机：只有「执行」进 agent、「跑」被 zero-tool）。
_GENERIC_EXEC_ACTION_WORDS = r"执行|跑"
_MUTATING_ACTION_WORDS = rf"{_MUTATING_EFFECT_WORDS}|{_GENERIC_EXEC_ACTION_WORDS}"
# 硬勘查词：本身即任务信号（读文件/搜索/分析…）。
_HARD_INSPECT_ACTION_WORDS = (
    r"分析|阅读|读取|查找|搜索|检索|比较|对比|总结|概括|梳理|汇总|审查|检查|排查"
)
# 软列举词：百科「Python有哪些特点 / 列出三国人物」也会命中；**只有**绑到工作区/文件
# 时才算干活（见 force_agent_work_signal）。决定 intent 时若未绑文件则从动作探测中剔除。
_SOFT_LIST_INSPECT_WORDS = r"列出|列一下|查看|看看|有哪些|浏览"
_INSPECT_ACTION_WORDS = rf"{_HARD_INSPECT_ACTION_WORDS}|{_SOFT_LIST_INSPECT_WORDS}"
_SOFT_LIST_INSPECT_RE = re.compile(_SOFT_LIST_INSPECT_WORDS)
# 强制进 agent 的硬信号：必须绑定**工作区/沙箱**语境。
# skeptic 二轮：裸「文件|文档」会把百科句打进 agent
# （C语言有哪些文件操作函数 / 文档有哪些类型 / 有哪些文件IO方法）。
# 只认工作区锚点：我的文件 / 文件区 / 文件列表 / 所有|全部文件 / 扩展名；
# 「有哪些文件」「列出文件」仅在 文件 后不是 操作|系统|IO|… 等术语复合时生效。
_FILE_TERM_COMPOUND = (
    r"操作|系统|IO|I/O|名|格式|类型|接口|函数|方法|结构|权限|路径|句柄|"
    r"描述符|指针|流|传输|协议|编码|属性|对象|类|API|api"
)
_FORCE_AGENT_SIGNAL_RE = re.compile(
    r"(?is)("
    r"\bbash\b|\bshell\b|沙箱|"
    r"我的文件|/workspace/|"
    r"写入|写出|写到|写进|写个|写一|保存到|存到|存进|保存为|存为|"
    r"新建|创建一份|生成一份|制作一份|导出|下载到|"
    # 工作区锚点（不含裸「文件/文档」——避免 文件操作/文档类型 百科误伤）
    r"(?:列出|列一下|有哪些|看看|查看).{0,16}(?:我的文件|文件区|文件列表|目录|"
    r"所有文件|全部文件|\.(?:md|txt|docx?|pptx?|xlsx?|pdf|csv))|"
    # 「有哪些文件 / 列出文件」：工作区列举；排除「文件操作/文件系统/文件IO…」
    r"(?:列出|列一下|有哪些|看看|查看)\s*文件(?!" + _FILE_TERM_COMPOUND + r")|"
    # 文件列表 / 我的文件里有哪些 / 文件区有哪些（明确工作区）
    r"文件列表|(?:我的文件|文件区).{0,6}(?:有哪些|列表|里|中)|"
    r"跑一下|跑通|跑起来|运行一下|执行一下|"
    r"用\s*bash|用\s*shell|"
    r"[\w一-龥][\w.\-一-龥]*\.(?:md|txt|docx?|pptx?|xlsx?|pdf|csv|html?|json|ya?ml|py|sh|zip|png|jpe?g)"
    r")"
)
_CASUAL_CHAT_RE = re.compile(
    r"^(你好|您好|嗨|哈喽|hello|hi|谢谢|感谢|早上好|下午好|晚上好|在吗|你好啊)"
    r"[。！!？?\s]*$",
    re.IGNORECASE,
)
_MATH_OR_TRIVIAL_RE = re.compile(
    r"^[\d\s\+\-\*/×÷=＝？?。.，,]+$"
    r"|^.{0,40}等于[多少几].{0,12}$"
    r"|^只用一句话回答"
)
# 用户显式关闭工具：才允许零工具。不靠领域词表猜「天气/新闻」。
_EXPLICIT_NO_TOOLS_RE = re.compile(
    r"(不要用工具|别用工具|无需工具|不用工具|不要工具|禁止用工具|"
    r"不要调用工具|别调用工具|不需要工具|别使用工具|不要使用工具)"
)
_ACTION_RE = re.compile(rf"({_MUTATING_ACTION_WORDS}|{_INSPECT_ACTION_WORDS})")
_MUTATING_ACTION_RE = re.compile(rf"({_MUTATING_ACTION_WORDS})")
_INSPECT_ACTION_RE = re.compile(rf"({_INSPECT_ACTION_WORDS})")


def _explicit_request_pattern(action_words: str) -> re.Pattern[str]:
    return re.compile(
        rf"((请|帮我|帮忙|给我|替我|麻烦|直接|需要你|想让你|你来).{{0,16}}({action_words}))"
        rf"|((^|[，,。；;！!？?])\s*((继续|接着)\s*)?(把\s*.{{0,20}})?"
        rf"({action_words})(一下|下|吧|掉|好|点)?)"
    )


_MUTATING_REQUEST_RE = _explicit_request_pattern(_MUTATING_ACTION_WORDS)
_MUTATING_EFFECT_REQUEST_RE = _explicit_request_pattern(_MUTATING_EFFECT_WORDS)
_GENERIC_EXEC_REQUEST_RE = _explicit_request_pattern(_GENERIC_EXEC_ACTION_WORDS)
_INSPECT_REQUEST_RE = _explicit_request_pattern(_INSPECT_ACTION_WORDS)
_EXPLICIT_REQUEST_RE = re.compile(
    rf"({_MUTATING_REQUEST_RE.pattern})|({_INSPECT_REQUEST_RE.pattern})"
)
_REVISION_ACTION_RE = re.compile(
    # 「换」必须排除"改变做法"义（换路/换个思路/换种方式/换一批关键词）——2026-07-29
    # 真机第三次误判：任务里一句「遇到抓取失败请自行换路」就把全新调研判成了修订。
    # 真修订的「换成/换掉/更换/替换」不受影响（后跟字不在负向表里）。
    r"(修改|改成|改(?!进|善|革)|调整|优化|完善|更新|编辑|重写|修复|精修|继续改|"
    # 「换」的负向表只排除**改变做法**义（换路/换种方式/换个思路…），不能连
    # 「换个/换一下」整个吃掉（2026-07-29 对抗审计）：「把这份 PPT 的配色换个暖色调」
    # 是最常用的修订说法，误判成非修订后 tool_scope 从 apply_presentation_patch 掉回
    # build_presentation → 模型另起一份新 PPT，「禁止另起副本」的提示词也一并消失。
    # 做法：负向只在「换个/换一」后面真的跟着做法类名词时才生效。
    r"换(?!路|查询|关键词|思路|方式|方法|角度|行)"
    # 「换 + (个|一批|一组|一种…) + 做法类名词」整体排除。注意量词与名词之间可能有别的字
    # （"换一批关键词"），所以量词后允许 0-4 个字再接名词——这条边界是被
    # test_revision_reality_check 抓出来的：放宽时只想着"换个暖色调"，忘了"换一批关键词"
    # 也是「换一」开头（第三次击穿的原形态）。
    r"(?!(?:个|一个|一下|一批|一组|一轮|种|一种)?\s*.{0,4}?"
    r"(?:思路|方式|方法|角度|说法|做法|路子|方向|关键词|查询|词|策略))\s*)"
)
_EXISTING_TARGET_RE = re.compile(
    # 「我的文件」是产品功能区名，只有后跟「里/中/下」（"我的文件里的 X"）才是指向
    # 既有内容；"保存到『我的文件』"这类去向话术不算——2026-07-29 真机误判：带中文
    # 引号的『我的文件』绕过了 _SAVE_DESTINATION_RE 的剔除，裸词命中把新建判成修订。
    r"(刚才|刚刚|之前|上次|原来|原文件|已有|现有|这个|这份|该文件|我的文件(?=里|中|下)|"
    r"第\s*\d+\s*(页|行|段|张)|ppt|pptx|文档|文件|报告|表格|页面|代码|"
    # 直呼文件名（如 report.md / 方案）也视为指向既有产物
    r"[\w一-龥][\w.\-一-龥]*\.(?:md|txt|docx?|pptx?|xlsx?|pdf|csv|html?|json|ya?ml|png|jpe?g|svg))"
    r"",
    re.IGNORECASE,
)
_NEW_ARTIFACT_RE = re.compile(
    r"(重新做一份|另做一份|再做一份|新建|全新|从头重做|重新创建|"
    # 新产物规格常同时写「第2页/第3页」，后者本身也像旧产物回指。只认
    # 明确带量词「一份/一个」的创建话术，不把泛泛的「生成内容」放进来。
    r"(?:制作|生成|创建|做|输出|导出).{0,16}(?:一|1)\s*(?:份|个))"
)
# 「保存到我的文件」「文件名：xxx.md」「保存为 xxx.md」是**新建产物的去向与命名**话术：
# 「我的文件」是产品功能区名，冒号/「为」后面的文件名是给新文件起的名字，都不指向已有产物。
# 仅从 existing_target 的探测文本里剔除，不参与写意图/授权判定。真机实证（2026-07-28，
# deepseek-v4-pro）：「整理一份选型建议保存到我的文件，文件名：xx.md」中「建议」触发
# feedback、「我的文件/xx.md」触发 existing_target，被误判 revise 且 allow_create=False，
# 修订目标又解析不出 file_id → 写工具全部被扣押，模型只能把正文贴在聊天里交差。
_SAVE_DESTINATION_RE = re.compile(
    # 第一分支放宽（2026-07-29）：「保存为 Markdown 到"我的文件"」——动词与「到」之间
    # 允许插「为/成 格式名」，「到」与「我的文件」之间允许引号（中英文四种）。
    r"(?:保存|存|放|导出|输出)(?:[为成]\s*\S{1,12})?\s*(?:到|进|入|至)\s*[「『“\"']?\s*(?:我的)?文件(?:区|库|夹|里|中)?"
    r"|(?:文件名|命名为|保存为|存为|另存为|保存成|导出为|输出为|取名)\s*[:：]?\s*[\w.\-一-龥]+\.\w{1,6}",
    re.IGNORECASE,
)
_DIRECT_EXEC_RE = re.compile(r"(直接执行|直接做|直接改|不用确认|不要再问|无需确认)")
_ADVICE_PHRASE_RE = re.compile(
    r"(?:(?:给出|提供|列出|整理|分析|说说|谈谈).{0,12})?"
    r"(?:改进|优化|修改|调整|完善)(?:的)?(?:建议|方案|思路|方向|方法)"
)
# 确认后执行层（V3 §九）：外部可见或不可逆动作，"先做"原则不适用，执行前必须确认。
# 2026-07-28 收紧两道（真机实证：调研任务问「发布时间」被标 requires_confirmation，
# 模型无端收到「执行前必须确认」指令）：
# ①动作词后排除高频名词后缀——「发布时间/提交记录/覆盖率/发布会」是名词性用法；
# ②必须呈请求形态（帮我发送/把 X 发布/句首命令），复用 _explicit_request_pattern，
#   动作词孤零零出现在名词短语中间不算数。danger.py 的参数级网关继续兜调用层的底。
_EXTERNAL_ACTION_WORDS = (
    r"(?:发送|发出去|发给|群发|转发|分享|发布|上线|推送|提交|部署|删除|清空|移除|销毁|覆盖)"
    r"(?!时间|日期|会|版|记录|历史|信息|状态|渠道|流程|规范|标准|说明|计划|次数|率|线|量|了吗|过吗)"
)
_EXTERNAL_REQUEST_RE = _explicit_request_pattern(_EXTERNAL_ACTION_WORDS)
_NO_WRITE_RE = re.compile(
    r"(?:只读|"
    r"(?:不要|禁止|无需|不需要|别).{0,4}"
    r"(?:修改|改动|编辑|写入|创建|新建|生成|保存|覆盖|删除)"
    r".{0,6}(?:任何|现有|已有)?(?:文件|代码|内容|产物)?)"
)
_ONLY_INSPECT_RE = re.compile(
    r"(?:只|仅)(?:需要你|请|帮我|帮忙)?(?:做|进行)?"
    r"(?:分析|查看|阅读|读取|审查|检查|排查|比较|对比|总结|梳理)"
    r"(?:一下|下)?"
)


def bare_control_message(message: str) -> bool:
    """这句话是不是一句**光秃秃的控制语**（继续 / 暂停 / 取消 / 停止，没别的内容）。

    2026-07-29：此前判断"是不是控制语"是拿 `authority not in (inspect, mutate)` 当代理
    判据的——把授权和语义混成一个信号，于是「继续」一旦需要写权限（扣押轮的出口），
    计划模式那道「控制语别变成一份计划报告」的守卫就静默失效。两个问题各自问一次，
    互不牵连。三条正则与 decide_turn 里的同源，不新造词表。
    """
    text = str(message or "").strip()
    if not text:
        return False
    return bool(
        _CONTINUE_RE.search(text) or _PAUSE_RE.search(text) or _CANCEL_RE.search(text)
    )


def write_denied_request(message: str) -> bool:
    """用户在这句话里**显式拒绝写入**吗（"只读" / "不要修改任何文件" / "只帮我分析一下"）。

    抽成公开纯函数只为一件事：harness_orchestrator 需要在 Harness 部署开关**之外**独立认出这条
    边界（用户的显式要求不该跟着运维开关一起失效，2026-07-29），而这个判断绝不能再抄一份
    词表——本仓库被"两处独立维护同一判据、随后单向漂移"这个形态咬过四次。decide_turn 内部
    也调它，两边永远同一判据。
    """
    text = str(message or "").strip()
    if not text:
        return False
    # 「重新做一份」压过「不要改」：用户要的是新产物，不是禁止一切写入
    # （与 decide_turn 里 `and not allow_create` 同源，改一处必须改这处）。
    if _NEW_ARTIFACT_RE.search(text):
        return False
    return bool(_NO_WRITE_RE.search(text) or _ONLY_INSPECT_RE.search(text))


def force_agent_work_signal(message: str) -> bool:
    """这句话是否含有「必须进工具循环」的硬信号（文件/沙箱/写入/列出/跑…）。

    2026-08-04 真机：intent 词表漏「跑/写入/列出」时 route=direct_answer → 零工具 →
    模型谎称「工具系统不可用」。硬信号与 intent 分类解耦，route/decide 两侧共用。
    """
    text = str(message or "").strip()
    if not text:
        return False
    return bool(_FORCE_AGENT_SIGNAL_RE.search(text))


def _action_probe_text(message: str) -> str:
    """从动作探测文本中剔除「未绑定文件的软列举词」。

    未 force_agent 时「有哪些/列出/看看」只是百科措辞，不应单独把回合打成 execute/agent。
    已 force_agent（列出我的文件 / 有哪些 .md）时保留全文，动作信号照常生效。
    """
    text = str(message or "")
    if force_agent_work_signal(text):
        return text
    return _SOFT_LIST_INSPECT_RE.sub("", text)


def is_pure_qa_message(message: str) -> bool:
    """封闭世界高置信 pure_qa：才允许零工具 direct_answer。

    2026-08-05 根本收窄（天气事故根治）：默认 agent（能力常驻，模型自决是否调工具）。
    仅三类返回 True——
      1) 寒暄
      2) 纯口算 / 极短算术
      3) 用户显式禁止工具（不要用工具 / 别用工具…）
    删除：len<=80 默认 pure、一般疑问句默认 pure、百科列举默认 pure。
    禁止用「天气|新闻|股价」领域词表做反面缝补——那种做法必然漏下一种说法。
    """
    text = str(message or "").strip()
    if not text:
        return True
    if bare_control_message(text):
        return False
    if force_agent_work_signal(text):
        return False
    if write_denied_request(text):
        # 「只分析不要改文件」仍可能要读文件/搜索——不走零工具
        return False
    # 显式禁工具优先：用户自己关掉能力面，才允许 pure
    if _EXPLICIT_NO_TOOLS_RE.search(text):
        return True
    if _CASUAL_CHAT_RE.search(text):
        return True
    if _MATH_OR_TRIVIAL_RE.search(text):
        return True
    return False


@dataclass(frozen=True)
class TurnDecision:
    intent: TurnIntent
    authority: ActionAuthority
    reason_code: str
    revision: bool = False
    allow_create: bool = True
    direct_execute: bool = False
    requires_confirmation: bool = False
    # 计划相（2026-07-27 用户拍板：任务模式换成计划模式，且不要 graph）。
    #
    # 计划模式**完全长在主循环里**：这一位为真时 authority 被压成 inspect —— 于是
    # `build_tools` 只给只读工具（Harness：工具集合本身即授权边界，不靠 prompt 求模型
    # 别动手），模型用只读工具勘查完直接把计划报告作为**回答**流式吐出来。
    #
    # 这样做顺带解决了「路由到任务模式思考太久」：原先 graph 路径要先阻塞跑完
    # investigate（最多 4 轮）+ plan_graph（1~2 轮）才出第一个字，最坏 95s 静默；
    # 现在计划就是正文，第一个 token 立刻就到。
    plan_mode: bool = False
    # Deep Research 也完全长在主工具循环里：必要时一次集中问最多 3 题，
    # 回答回灌后沿用同一游标继续搜索、阅读与验证。
    research_profile: bool = False

    def route(self, *, has_explicit_resources: bool = False, message: str = "") -> TurnRoute:
        """routing is separate from authority: only closed-world pure Q&A is tool-free.

        2026-08-04：conversation 默认不再一律 direct_answer——漏检的干活话术会零工具假故障。
        2026-08-05 根本收窄：direct_answer 仅寒暄/口算/显式禁工具；其余默认 agent，
        工具常驻由模型自决（ChatGPT 式）。禁止用领域词表缝补 pure_qa。
        """
        if self.plan_mode:
            return "planning"
        if self.intent in ("pause", "cancel", "replace"):
            return "cancel"
        if self.intent == "continue":
            return "resume"
        if self.intent == "steer":
            return "steer"
        if self.research_profile or has_explicit_resources:
            return "agent"
        msg = str(message or "")
        if force_agent_work_signal(msg):
            return "agent"
        # pure_qa 先于 intent==execute：decide_turn 可能因软列举词误标 execute。
        if msg and is_pure_qa_message(msg):
            return "direct_answer"
        if self.intent in ("execute", "revise"):
            return "agent"
        # Feedback that already resolved to revise is handled above; remaining feedback may
        # still need tools. Pure chat stays tool-free.
        if is_pure_qa_message(msg):
            return "direct_answer"
        # Ambiguous conversation/feedback: prefer agent so core tools stay available.
        if self.intent in ("conversation", "feedback") and msg:
            return "agent"
        return "direct_answer"

    def prompt_block(self) -> str:
        """给模型的公开行为约束；只描述授权边界，不暴露分类推理。"""
        if self.research_profile:
            return (
                "**本轮是 Deep Research：先对齐研究边界，再系统研究。**\n"
                "平台用阶段机推进（澄清 → 计划 → 分主题检索/深读 → 交叉验证 → 报告合成），"
                "你只执行当前阶段，不要自决跳过。\n"
                "1. 先判断用户已给出的目标、范围、时间/地域、对比对象、评估维度和交付形式是否足以开始。\n"
                "2. 只有缺失信息会明显改变研究路线或结论时，才调用一次 `ask_user_choice`："
                "使用 `questions` 一次提出 2–3 题（最多 3 题），每题给 2–4 个互斥、易选的选项，"
                "有合理默认时标记 recommended。不要一题一题连环追问，不要为凑数量问已能合理推断的事。\n"
                "3. 如果需要澄清，用户回答回灌后用一句话确认研究边界；然后直接继续，"
                "不再要求用户确认计划。需求已经清楚时立即开始，不要为了展示流程而提问。\n"
                "4. 调用 `update_plan` 拆成 4–8 个可验证的研究步骤，再使用现有的搜索、"
                "知识库、文件和 Bash 能力执行。资料获取一律用 `search_web` 多角度换词检索"
                "（结果已含摘要与可用正文，不要深读或抓取网页）。"
                "按研究维度**多角度换词**检索（search_web 不设次数硬顶，够用即停）："
                "国内外/竞品/评测/榜单等分次检索；某次无结果就换角度再搜，"
                "勿空口宣称「单轮上限/调用上限」。每个主题至少两个独立站点的来源才能标完成；"
                "交叉验证完成后再停止搜索并在对话中给出结论，"
                "不要为凑次数而空转。`browser_fetch` 只用于用户点名给出的网址，不要拿它逐条打开"
                "搜索命中的链接。不要只搜一次就下结论；关键事实优先一手/官方资料，"
                "至少用另一个独立来源交叉验证，并注意发布日期与事件日期。\n"
                "5. 最终交付要可核查：先给结论摘要，再给核心发现/对比、证据与局限和建议；"
                "关键结论必须带可追溯引用，明确区分事实、来源主张与你的推断。"
                "把完整研究报告写在对话正文里，**必须使用 Markdown 井号标题**"
                "（# 报告标题，## 执行摘要 / 核心发现 / 证据与局限 / 建议）；"
                "本轮覆盖普通对话「禁止井号标题」的规则，不要改用加粗短语冒充标题。"
                "平台会把这段 Markdown 正文直接渲染成对话内蓝框报告。\n"
                "6. 不要修改、删除用户的项目文件；不要自己 write_file 或自动保存"
                " Word/HTML 调研文档。对话正文就是研究报告；复制、Markdown/Word/PDF 导出只由"
                "用户在蓝框报告的下载菜单中主动触发。"
            )
        if self.plan_mode:
            return (
                "**本轮是计划模式：先规划，不动手。**\n"
                + PLAN_CONTENT_CONTRACT
                + "\n\nAXIOM的 `update_plan` 是任务协作面板的结构化事实源，"
                "而 `<proposed_plan>` 是给用户和下一执行 Agent 阅读的完整实施说明；两者不是两套计划。"
                "勘查和必要澄清完成后，先调用一次 `update_plan` 写入 3–7 个有序、可验收步骤"
                "（每步含 title / detail / acceptance，最多一个 in_progress），再输出与其语义一致的完整计划块。"
                "不要为了同步状态反复调用 `update_plan`。\n"
                "计划块写完后，**必须调用 `ask_user_choice` 出一张确认卡**收尾："
                "question 用一句话问「这份计划可以开始执行吗」，options **只给「开始执行」一个选项**。"
                "不要自造「转成 Word / 就这样交付 / 调整某些内容」这类选项——"
                "改计划由用户在确认卡里直接补充，平台会渲染步骤、输入框、「开始执行」和「跳过」。"
                "计划正文写在你的回答里，卡片只承担确认与补充。即使你漏调，平台也会强制挂起，等用户点「开始执行」才动手。"
                "用户点「跳过」只表示计划先留着、本轮不执行，之后继续聊天；不要把跳过当成开始干活。\n"
                "**本轮到此为止**——不要创建、编辑、覆盖任何文件，也不要执行会改变项目状态的命令。"
                "用户点「开始执行」或自己输入「执行计划」「实施此计划」之后的下一轮才动手，那一轮你会拿到完整的执行能力。"
                "用户点「开始执行」时会发来「好，请执行此计划。」。"
                "用户在确认卡里补充意见后，仍停留在计划轮：再 `update_plan`、写出**一整份新的计划报告**（新卡片），"
                "再调用 `ask_user_choice` 重新挂起确认——用户可以再执行、再补充，或跳过。不要动手。\n"
                # 2026-07-29 真机：PPT 技能的 SKILL.md 写着「用 bash 执行打包脚本」，
                # 模型在计划轮照做 → bash 这一轮物理不存在 → 拿到「未知工具」纠错再换路。
                # 每次走到这里都白烧一个模型往返，而只读边界本身是对的。
                # 所以要明说「技能里的执行步骤留到执行轮」——第 7 条只写了"不要动手"，
                # 而模型面前还摆着一份让它动手的技能文档，两个指令冲突时它跟着更具体的那个走。
                "如果本轮挂载了技能（SKILL.md），**只读它、按它规划**："
                "里面写的执行动作（跑脚本、装依赖、生成文件）属于执行轮，本轮一个都不要做。"
                "**本轮的工具集里根本没有那些执行类工具**，调用它们只会拿到「未知工具」——"
                "不要试，把它们写进计划的对应 Phase 里。"
            )
        if self.authority != "mutate":
            return (
                "用户本轮明确要求只查看、只分析或不要修改。可以读取必要资料并回答，但不得"
                "创建、编辑、覆盖或删除文件。不要把这称为某种会话模式，也不要暗示后续轮仍受限制。"
            )
        confirm_suffix = (
            "注意：本次请求涉及发送、发布、分享、删除、覆盖或提交等外部/不可逆动作，"
            "这一步执行前必须先向用户确认；其余可逆步骤照常直接做。"
            if self.requires_confirmation else ""
        )
        if self.intent == "feedback":
            return (
                "用户表达了对当前内容或产物的意见。若上下文能唯一定位受影响对象，就直接做"
                "最小、可逆的改进并说明改了什么；若没有可操作对象，则先回应意见，不要凭空创建产物。"
                + confirm_suffix
            )
        if self.reason_code == "explicit_inspection":
            return (
                "用户本轮明确要求分析、读取、检查或总结。写工具仍是正常对话的常驻能力，"
                "但本轮目标不是修改；只读取完成分析，不要主动改动、创建或覆盖文件，"
                "也不要把这描述成某种只读模式。"
            )
        if self.revision and not self.allow_create:
            return (
                "本轮是修改既有产物。必须先定位并读取原文件，在原文件基础上只修改受影响部分；"
                "禁止另起一份替代品，禁止从头重做未受影响部分。改动会记录版本历史、可随时撤销；"
                "目标无法唯一确定时先让用户选择。" + confirm_suffix
            )
        return (
            "本轮已获得执行授权。先做而不是先问：影响范围明确且可逆的动作直接做"
            "（文件改动有版本历史兜底），只在目标存在真实歧义时才追问。" + confirm_suffix
        )


# 修订目标未解析时的行为约束（harness_orchestrator 注入 turn_guard_prompt）。
# 2026-08-05：写/执行工具在 build_tools 层**物理收起**（工具集合=授权边界）。
# prompt 只解释「为何未开放」并要求 ask_user_choice；不得再写「工具仍在场」。
REVISION_TARGET_UNRESOLVED_GUIDANCE = (
    "本轮要修改的目标文件未能唯一确定。"
    "**写/执行工具本轮未开放**（不是坏了，是 harness 在目标确认前物理收起，"
    "避免 prompt 说「先问」而工具仍允许乱写）。"
    "你此刻的正确路径：先用 glob/read_file 找出候选文件，"
    "然后**必须**调用 ask_user_choice 出一张选择卡让用户点选目标"
    "（不要在正文里口头追问，正文追问没有确认语义、下一轮仍可能再次歧义）。"
    "用户确认后会进入下一轮并重新开放写工具；确认前不要声称已完成修改、"
    "不要另起新文件、不要把修改内容贴在聊天里交差。"
)


def build_revision_candidate_guidance(candidates: list) -> str:
    """多产物歧义时，把候选文件名硬塞进 turn_guard，逼 ask_user_choice 选项集合闭合。

    平台已经知道候选却只说「去问用户」，模型仍会自由发挥选项文案——选项与真实
    文件名对不上时，确认卡点了也绑不到 revision_target。把「恰好这些文件名」写成
    硬约束，是 harness 层消歧，不是文案优化。
    """
    names: list[str] = []
    for item in candidates or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("filename") or item.get("name") or "").strip()
        if name and name not in names:
            names.append(name)
        if len(names) >= 8:
            break
    if len(names) < 2:
        return ""
    listed = "、".join(f"「{n}」" for n in names)
    return (
        f"会话里有多份可能相关的产物：{listed}。"
        "调用 ask_user_choice 时 options 必须**恰好**使用这些文件名作为 label"
        "（可另加一项「以上都不是/我指定其他文件」），不要改写成摘要、不要合并、"
        "不要省略扩展名。"
    )


def resolve_revision_target_from_choice(choice: str, candidates: list) -> dict | None:
    """把用户点选/自由文本映射到 revision_target。

    平台硬闸在目标未解析时收起写工具；用户确认后必须在 resume 侧解锁同一目标，
    否则会出现「点了确认卡，下一轮仍不能写」的 harness 自相矛盾。
    """
    text = str(choice or "").strip()
    if not text:
        return None
    items: list[dict] = []
    for item in candidates or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("filename") or item.get("name") or "").strip()
        fid = str(item.get("file_id") or item.get("id") or "").strip()
        if name and fid:
            items.append({"filename": name, "file_id": fid})
    if not items:
        return None
    lower = text.lower()
    # 1) 精确文件名
    for item in items:
        if item["filename"] == text or item["filename"].lower() == lower:
            return {**item, "source": "user_choice"}
    # 2) 选项值里包含文件名 / 文件名包含选项
    for item in items:
        name = item["filename"]
        if name in text or text in name or name.lower() in lower:
            return {**item, "source": "user_choice"}
    # 3) stem 匹配（去掉扩展名）
    for item in items:
        stem = item["filename"].rsplit(".", 1)[0]
        if len(stem) >= 2 and (stem == text or stem.lower() == lower or stem in text):
            return {**item, "source": "user_choice"}
    return None


# 扩展名与 _EXISTING_TARGET_RE 第三支对齐（2026-07-29 深扫：原先少 json/yml/png/svg 等，
# 那几族能触发 revise 却结构性触发不了安全网——安全网对它们是死代码）。
_NAMED_FILE_RE = re.compile(
    r"[\w\-一-龥][\w.\-一-龥]{0,80}"
    r"\.(?:md|txt|docx?|pptx?|xlsx?|pdf|csv|html?|json|ya?ml|png|jpe?g|svg|webp|gif)\b",
    re.IGNORECASE,
)

# **指代既有产物的证据**（2026-07-29 治本判据）。
# 词表被击穿四次的根因是 _EXISTING_TARGET_RE 收录了「文档|报告|表格|ppt|代码」这类
# **新交付物的类名**——于是"是否在改已有东西"退化成"句子里有没有一个动作词"，
# 「帮我写一份整改报告」被判成修订、写工具全体扣押（实测 12/15 中性话术命中）。
# 正确的判据是**有没有指向"先前那个东西"的证据**：明确的回指词、或点名一个真实存在的
# 文件、或用户在 composer 里勾了文件。类名词一律不算证据。
_ANAPHORIC_TARGET_RE = re.compile(
    r"(刚才|刚刚|之前|上次|上一(?:个|份|版|条)|原来|原文件|原文|原稿|已有的|现有的|"
    r"这份|这个文件|这篇|该文件|那份|那个文件|上面(?:那|这)|你(?:刚|之前)(?:做|写|生成|改)|"
    r"我的文件(?=里|中|下)|第\s*\d+\s*(?:页|行|段|张)|"
    # **指示代词 + 类名词**（2026-07-29 真机复测补）：类名词单独出现不算证据
    # （"帮我写一份整改报告"是新建），但「那个报告」「这个 PPT」「上述方案」是明确指向
    # 先前那个东西——真机实测「把那个报告改简洁一点」原先漏判成新建，模型拿到写工具后
    # 只是自己 glob 了一下、在正文里追问，**换个更自信的模型就可能直接覆写错文件**。
    # 「该」单列且只收固定搭配（2026-07-29 对抗审计）：中文里「该」更常是"应该"，
    # 「该说明的地方说明清楚」「该调整的就调整」会被当成回指 → 纯新建请求被扣押。
    r"(?:这|那|上述|前述)(?:个|份|张|篇|版|条)?\s*"
    r"(?:报告|文档|文件|表格|方案|计划|清单|说明|总结|纪要|稿子?|PPT|ppt|pptx|"
    r"演示文稿|简历|周报|日报|月报|预算|合同|协议|论文|代码|脚本|图|表)|"
    r"该(?:文件|报告|文档|表格|方案|PPT|ppt|页|段))"
)


def anaphoric_target_reference(message: str) -> bool:
    """消息里有没有指向「先前那个产物」的回指证据（类名词不算）。

    取证前**先剔除去向话术**（2026-07-29 对抗审计 P0）：「保存到我的文件里」才是
    「我的文件里」最常见的说法，而 `我的文件(?=里|中|下)` 那条前瞻本意是收
    「我的文件里的 X」这种回指——方向正好搞反，于是
    「整理一份竞品分析报告，优化一下措辞，保存到我的文件里」这种**纯新建**请求
    被判成"有回指证据"→ 扣押写工具、甚至去覆盖上一份产物。
    _EXISTING_TARGET_RE 那侧早就用同一个正则剔过，新写的这条治本判据漏了。
    """
    text = _SAVE_DESTINATION_RE.sub("", str(message or ""))
    return bool(_ANAPHORIC_TARGET_RE.search(text))


def named_files(message: str) -> list:
    """消息里点名的**全部**带扩展名文件名（按出现顺序，全角点号已归一）。

    两处修正（2026-07-29 深扫）：①用 finditer 而不是 search——原实现只看第一个名字，
    「参考 report.md 更新 新周报.md」会因首名存在而错过安全网；②中文输入法常打出全角
    **点号**（U+FF0E "．"，如"报告．md"），不归一则整个正则不命中，判据对它是瞎的。

    ⚠️ 只归一全角点号，**不碰中文句号**（U+3002 "。"）：我一度把 "。md" 也替换成 ".md"，
    于是「我们讨论完了。md 文件在哪」被识别成文件名"我们讨论完了.md"——而"点名了文件"
    正是 references_file_context 取最近附件当修订目标的入口之一，等于给刚堵上的附件劫持
    留了个后门（2026-07-29 自查抓到）。句号是句子边界，本来就不是点号。
    """
    text = str(message or "").replace("．", ".")
    return [m.group(0) for m in _NAMED_FILE_RE.finditer(text)]


def _normalize_names(names) -> set:
    out = set()
    for n in names or set():
        s = str(n or "").strip().lower()
        if s:
            out.add(s)
            # 全角点号也认（真机曾见「报告．md」绕过匹配）
            out.add(s.replace("．", "."))
    return out


def named_existing_file(message: str, existing_names) -> str:
    """消息点名的文件里，第一个**确实存在**于给定名单的（有则是修订证据）。"""
    lowered = _normalize_names(existing_names)
    for name in named_files(message):
        if name.strip().lower().replace("．", ".") in lowered:
            return name
    return ""


def named_missing_file(message: str, existing_names) -> str:
    """消息点名了文件、但**没有任何一个**存在于名单时，返回第一个名字。

    返回空串表示不满足条件（没点名文件，或点名的文件里至少有一个真实存在）。
    """
    names = named_files(message)
    if not names:
        return ""
    return "" if named_existing_file(message, existing_names) else names[0]


def revision_lock_justified(
    message: str,
    *,
    has_selected_files: bool,
    existing_names=None,
) -> bool:
    """「禁止新建、只能改已有产物」这条硬约束是否站得住。

    站得住的三种证据（任一即可）：①用户在 composer 里勾了文件；②消息里有明确回指
    （"刚才那份"/"原文件"/"第 3 页"）；③消息点名了一个**真实存在**的文件。
    三者都没有时，"修订"是词表误判的产物——按新建放行写工具，绝不扣押。
    """
    if has_selected_files:
        return True
    if anaphoric_target_reference(message):
        return True
    return bool(named_existing_file(message, existing_names or set()))


def decide_turn(
    message: str,
    *,
    has_selected_files: bool = False,
    active_run: bool = False,
) -> TurnDecision:
    text = str(message or "").strip()
    if not text:
        return TurnDecision("conversation", "none", "empty")

    if _REPLACE_RE.search(text):
        return TurnDecision("replace", "mutate", "replace_active_run", direct_execute=True)
    # 暂停/取消：**无条件**保持控制语义（authority="none"）。它们是"别做了"，任何时候都不该
    # 被解读成写请求；harness_orchestrator 恒传 active_run=False，把它们挂在 active_run 上会让
    # 计划模式那道「控制语不要变成一份计划报告」的守卫变成死代码
    # （2026-07-28 修过的 bug，2026-07-29 我一度改回去，被 test_plan_resume_guard_rewrite 抓住）。
    if _PAUSE_RE.search(text):
        return TurnDecision("pause", "none", "pause_control")
    if _CANCEL_RE.search(text):
        return TurnDecision("cancel", "none", "cancel_control")
    # 「继续」不同：它是**推进**而不是停止。有进行中 Run 时是 Run 控制词（恢复挂起的那个）；
    # 没有时它的意思是"接着把活干完"，需要完整写能力——曾经这里无条件返回 authority="none"，
    # 而扣押轮的引导语正是教用户回复「继续」开下一轮，写工具却摘得比扣押时更干净，
    # 我们亲手把唯一出口做成了陷阱（2026-07-29 深扫实锤）。
    #
    # ⚠️ 但 authority 不能同时当"是不是控制语"的判据用：计划模式开关那道守卫
    # （harness_orchestrator，2026-07-28）原先靠 `authority not in (inspect, mutate)` 认控制语，
    # 于是这里放开写权限就把那道守卫变成死代码——用户打「继续」会收到一份完整计划报告。
    # 两件事分开：授权按语义给（mutate），"是不是裸控制语"由 bare_control_message() 单独答，
    # 守卫改用后者。
    if active_run and _CONTINUE_RE.search(text):
        return TurnDecision("continue", "none", "continue_control")

    # 未绑文件的软列举词不参与「有动作 / 显式勘查」判定（百科句不得进 execute）。
    action_probe = _action_probe_text(text)
    action_mentioned = bool(_ACTION_RE.search(action_probe))
    explicit_action = action_mentioned and bool(_EXPLICIT_REQUEST_RE.search(action_probe))
    explicit_inspection = bool(_INSPECT_ACTION_RE.search(action_probe)) and bool(
        _INSPECT_REQUEST_RE.search(action_probe)
    )
    # “给出优化建议 / 改进方案”是分析交付物，不是授权直接改代码。先从写权限信号中
    # 摘掉这类咨询短语，再判断是否仍存在真实写动作。
    mutation_signal_text = _ADVICE_PHRASE_RE.sub("", text)
    explicit_effect_mutation = bool(_MUTATING_EFFECT_REQUEST_RE.search(mutation_signal_text))
    explicit_generic_execution = bool(_GENERIC_EXEC_REQUEST_RE.search(mutation_signal_text))
    explicit_mutation = explicit_effect_mutation or (
        explicit_generic_execution and not explicit_inspection
    )
    question = bool(_QUESTION_RE.search(text))
    feedback = bool(_FEEDBACK_RE.search(text))
    allow_create = bool(_NEW_ARTIFACT_RE.search(text))
    # 走公开纯函数（不是就地再写一遍正则）：harness_orchestrator 要在部署开关之外认同一条边界，
    # 两边同源才不会漂移。口径逐字等价于原式（含 `and not allow_create`）。
    write_denied = write_denied_request(text)
    if write_denied:
        explicit_mutation = False
    # 先剔除「保存到我的文件 / 文件名：xxx.md」类新建去向话术，再探测是否指向既有产物
    target_probe_text = _SAVE_DESTINATION_RE.sub("", text)
    existing_target = has_selected_files or bool(_EXISTING_TARGET_RE.search(target_probe_text))
    # 确认后执行层（V3 §九）：请求形态的外部/不可逆动作 + 未显式豁免。**独立于写词表**判定：
    # 「发送/清空/转发/分享」不是文件写动作、不在 _MUTATING_* 词表里，但正是最需要
    # 先确认的一类——不能要求先命中 explicit_mutation 才有确认语义（2026-07-28 实测
    # 「帮我把这份报告发送给张三」走 conversation 分支，确认标记整个丢失）。
    external_confirmation = (
        not write_denied
        and bool(_EXTERNAL_REQUEST_RE.search(text))
        and not bool(_DIRECT_EXEC_RE.search(text))
    )
    revision = existing_target and (
        bool(_REVISION_ACTION_RE.search(mutation_signal_text))
        or feedback
    )
    if write_denied:
        revision = False
        allow_create = False

    # 正常对话能力常驻：只有用户显式拒绝写入时才物理剥离写工具。“如何修改”仍是咨询
    # 意图，但工具可用性不再由“普通/任务模式”决定，模型按用户目标选择是否调用。
    if question and not (explicit_mutation or explicit_inspection):
        return TurnDecision(
            "conversation",
            "inspect" if write_denied else "mutate",
            "question_no_write" if write_denied else "question",
            allow_create=not write_denied,
        )

    # 中文对象前置命令常不带“帮我/把”，例如“给这份文件的颜色换成蓝白色调”。
    # 只要出现真实写动作、且不是建议短语/意见句/疑问句，就按明确执行处理。
    object_first_mutation = bool(_MUTATING_ACTION_RE.search(mutation_signal_text)) and not (
        question or feedback or write_denied
    )
    if explicit_action or object_first_mutation:
        intent: TurnIntent = "revise" if revision and not allow_create else "execute"
        if active_run:
            intent = "steer"
        authority: ActionAuthority = "inspect" if write_denied else "mutate"
        return TurnDecision(
            intent,
            authority,
            (
                "explicit_revision"
                if revision
                else "explicit_mutation"
                if explicit_mutation or object_first_mutation
                else "explicit_inspection"
            ),
            revision=revision,
            allow_create=allow_create or not revision,
            direct_execute=bool(_DIRECT_EXEC_RE.search(text)),
            # 只在真实写授权下立确认标记；用户明说「直接执行/不用确认」已在
            # external_confirmation 里豁免——那本身就是确认。
            requires_confirmation=external_confirmation and authority == "mutate",
        )

    if feedback:
        return TurnDecision(
            "feedback",
            "inspect" if write_denied else "mutate",
            "feedback_no_write" if write_denied else "feedback",
            revision=revision,
            allow_create=False if revision else not write_denied,
            requires_confirmation=external_confirmation,
        )

    # 活动 Run 中的普通补充说明交给当前 Run（任务内调整=可逆修改层）。
    if active_run:
        return TurnDecision("steer", "mutate", "active_run_guidance")

    # 硬信号强制干活（与 route() 同源）：intent 词表漏检时仍给 execute，便于 pin 核工具
    # 与 turn_guard 走执行口径，而不是 conversation + 零工具。
    if force_agent_work_signal(text) and not write_denied:
        intent_forced: TurnIntent = "revise" if revision and not allow_create else "execute"
        return TurnDecision(
            intent_forced,
            "mutate",
            "work_signal_force_agent",
            revision=revision,
            allow_create=allow_create or not revision,
            direct_execute=bool(_DIRECT_EXEC_RE.search(text)),
            requires_confirmation=external_confirmation,
        )

    return TurnDecision(
        "conversation",
        "inspect" if write_denied else "mutate",
        "conversation_no_write" if write_denied else "conversation",
        allow_create=not write_denied,
        # conversation 轮同样有写工具在手：外部动作请求（发送/清空类动词不在写词表、
        # 到不了 explicit_action 分支）落到这里时，确认语义不能丢
        requires_confirmation=external_confirmation,
    )
