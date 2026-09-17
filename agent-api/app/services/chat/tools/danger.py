# -*- coding: utf-8 -*-
"""危险动作识别（2026-07-27）——审批网关的判定层。

## 为什么不是「按工具名标 sensitive」

`MainTool.sensitive` 是**静态**的：标了就每次都要审批。真按工具名标，最该标的 `bash`
会立刻把产品打死——生成一份 PPT 要调十几次 bash（`ls` / `cat` / `python3 build.py`），
每次都弹确认框，没人受得了。

真正决定危不危险的是**参数**，不是工具名：`bash ls /workspace/files` 和
`bash rm -rf /workspace/files` 是同一个工具的两次调用，风险差着数量级。

所以这里做的是：**看着参数判**，只拦真正不可逆或对外有副作用的那几种，其余一律放行。
判定为危险时返回一句**给用户看的中文理由**（审批卡上要显示它），否则返回 None。

## 判据（只收三类，宁可漏也不要烦）

1. **不可逆的破坏**：删除、覆盖设备、格式化、递归改权限。
   ⚠️ 破坏面是**本轮沙箱**，不是用户文件区（2026-07-27 更正，原先这里写反了）：回写只比对
   「执行后清单」与基线的 (size, mtime)，**被删掉的文件根本不在执行后清单里，一次都不会
   被回写**（`sandbox_executor` 的 collect_workspace 分支）。所以 `rm -rf /workspace/files` 毁掉的
   是本轮同步进来的材料和尚未落库的中间产物——白烧掉一整轮工作，仍值得问一句，
   但**不要再对用户说"这会删掉你的文件"**，那是假的。
2. **把外部代码拉进来直接执行**：`curl … | sh` 这类。沙箱可按部署网络策略出网，所以这条必须始终进入审批；它是从"读网页"到"执行任意代码"的最短路径。
3. **对外产生副作用的浏览器交互**：提交表单、点击提交/支付/删除类按钮。点击不是读取——
   它可能发出一封邮件、下一个单。这条是 `browser_act` 的主要约束来源。

## 明确不收的（写清楚，免得以后有人"顺手补上"）

- 普通写文件（`write_file` / `edit_file` / `download_url`）：用户文件区有**版本历史**，
  一键回上一版，不属于不可逆。为它们弹确认只会训练用户闭眼点"通过"。
- 普通 `bash`：见上。
- `forget_memory`：用户自己让它忘的，弹框问"确定要忘吗"是废话。

## 这道闸拦不住什么（别把它当沙箱）

参数匹配天生绕得过去，下面这些**已知漏**，写在这里免得有人把它当安全边界依赖：
`find … -delete`、`python3 -c "shutil.rmtree(…)"`、`X=rm; $X -rf …`（变量间接）、
`eval`、`$'\\x72\\x6d'` 这类编码拼接。真正的边界是**容器本身**（非 root、按策略限制网络、
资源上限、用完即弃）和**回写层**（`WorkspaceSync.persist`：只回写新增/修改，
且原位修改轮次只回写授权的那个 file_id）。这里只负责让**显眼的**那几类停下来问一句。

浏览器这边还有一条同类的漏（2026-07-28 记）：`browser_act` 的 `element` 是**模型
自己填的一句描述**，不是页面上的真实文案——把「提交订单」写成「继续」就绕过了
关键词表。要真正堵住，得拿 `target`（ref 句柄）回查上一次 a11y 快照里的真实元素名，
也就是把页面状态带进本函数；本函数目前是纯函数、只看参数，改签名会波及审批网关
整条链路。press+Enter 那条已经补上（它是**结构性**的绕过，与描述文案无关），
描述可信度这条仍然敞着，钉在 tests/test_danger_gate.py 里。
"""
from __future__ import annotations

import re
from typing import Any, Optional

# ---- 1. 不可逆破坏 ----
# 逐条都要能说出"它毁掉什么"，不做泛化的"危险关键词"匹配（那种表最后一定会误伤正常命令）
_DESTRUCTIVE = [
    # 只拦递归删除。`rm -f file.jpg` 是幂等的单文件清理，PPT 等产物流程会频繁用它
    # 清掉临时素材；把它也拦成“递归/强制删除”会留下与交付无关的审批卡。这里仍要识别
    # `rm -rf x` / `rm --recursive --force x` / `rm x -rf`（GNU getopt 默认允许选项后置），
    # 所以不能只匹配「rm 后紧跟 -r」，要在**同一段命令内**找 -r/-R/--recursive。
    # 命令分隔符（; & | 换行）不跨越：否则 `rm a.txt && ls -f` 会把后一条的 -f 算到 rm 头上。
    # `(?![./\w-])` 挡形近词：`rm.bak`、`rm-old`、`/path/rmdir` 不算。
    (re.compile(
        r"\brm\b(?![./\w-])[^\n;&|]*?\s(?:-[a-zA-Z]*[rR][a-zA-Z]*|--recursive)\b"
    ), "递归删除文件"),
    (re.compile(r"\bshred\b"), "抹除文件内容"),
    (re.compile(r"\bmkfs(\.|\s)"), "格式化文件系统"),
    (re.compile(r"\bdd\b[^\n]*\bof=/dev/"), "直接写入块设备"),
    (re.compile(r">\s*/dev/(sd|nvme|disk)"), "覆盖块设备"),
    (re.compile(r"\bchmod\s+-R\s+777\b"), "递归放开全部权限"),
    (re.compile(r"\bgit\s+(push\s+--force|reset\s+--hard)\b"), "不可逆的 git 操作"),
    (re.compile(r":\(\)\s*\{\s*:\|:&\s*\};:"), "fork 炸弹"),
]

# ---- 2. 把外部代码拉进来直接执行 ----
# **管道进 shell 与这段字节从哪来无关**：`cat cmd.txt | sh`、`… | base64 -d | sh` 和
# `curl … | sh` 是同一件事——把上一段的输出当代码跑。原先只认 curl/wget 开头，于是这条
# 自称"最短的一条从读网页到执行任意代码的路"的规则被一个 `cat` 就架空了（2026-07-27 实测）。
# 只对 **shell 解释器**（sh/bash/zsh/dash/ksh）做任意来源的泛化：`… | python3` 常常是正经
# 数据管线（`cat a.csv | python3 clean.py`），一起泛化会天天误弹，那时网关等于不存在。
# `(?<!\|)\|(?!\|)`：`a || sh -c x` 是逻辑或不是管道，不收。
_PIPE_TO_SHELL = re.compile(
    r"(?<!\|)\|(?!\|)\s*(?:sudo\s+)?(?:ba|z|da|k)?sh\b|"
    r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:sudo\s+)?(?:python3?|node|perl|ruby)\b"
)

# ---- 3. 浏览器交互里对外有副作用的动作 ----
# 只读导航（点链接翻页、展开全文、切标签）不在此列——那是"读"，不是"做"。
_MUTATING_BROWSER_ACTS = {"submit", "upload", "download", "dialog"}
# 「按下去就等于提交」的键（2026-07-28 补）。焦点在表单里按 Enter 就是 submit，
# 浏览器行为如此——而 `action="press", text="Enter"` 既不在 _MUTATING_BROWSER_ACTS 里，
# element 又允许留空（browser_act 只对非 back 动作要求 target，press 连 target 都不看），
# 于是整条审批链一次都不触发：这是 submit 审批最短的一条绕过路径（2026-07-28 审计）。
# browser.py 的 _act 在 press 不给 text 时**默认按 Enter**，所以空值也要按 Enter 判。
_SUBMIT_KEYS = {"enter", "numpadenter", "return", "\n", "\r"}
# 元素描述里出现这些词，说明这一下点下去大概率对外发生了什么
_RISKY_ELEMENT_WORDS = (
    "提交", "确认", "支付", "付款", "下单", "购买", "结算", "删除", "移除", "注销",
    "发送", "发布", "同意", "授权", "绑定", "解绑", "转账", "提现", "充值",
    "submit", "confirm", "pay", "checkout", "purchase", "delete", "remove",
    "send", "publish", "authorize", "transfer", "withdraw",
)


# 任意命令执行器：参数级危险判据对它们一视同仁（2026-07-29）。
# 现在只有 bash（旧 execute_in_sandbox 工具整套下线），但**保留这个集合而不是写死 `== "bash"`**：
# 当天早些时候这里就是硬编码单个名字，结果回退形态里的 execute_in_sandbox 成了同一条
# `rm -rf` 的免审批同义入口（实测放行）。将来再加任何能执行任意命令的工具，
# 加进这个集合即可，不要再新写一条 if。
_SHELL_LIKE_TOOLS = frozenset({"bash"})


def _text_of(args: Any, *keys: str) -> str:
    if not isinstance(args, dict):
        return ""
    return " ".join(str(args.get(k) or "") for k in keys)


def assess(tool_name: str, args: Any) -> Optional[str]:
    """需要审批就返回一句中文理由；不需要返回 None。

    这个函数**只读参数、不产生任何副作用**，可以随便重复调用。
    """
    name = str(tool_name or "")

    # 判据按**能力**而不是工具名（2026-07-29 深扫 P0）：SANDBOX_WORKSPACE_SYNC=false 的
    # 回退形态里 execute_in_sandbox 与 bash 同时注册，而这里原先只认 "bash" —— 同一条
    # `rm -rf /workspace` 走 bash 弹审批卡、写成 execute_in_sandbox(code="os.system('rm -rf …')")
    # 直接放行（实测）。任意命令执行器都要过同一套参数级判据；参数名不同（command/code）
    # 由 _text_of 的 keys 兜住。新增执行器工具时加进这个集合，不要再新写一条 if。
    if name in _SHELL_LIKE_TOOLS:
        command = _text_of(args, "command", "code", "script")
        if not command.strip():
            return None
        for pattern, why in _DESTRUCTIVE:
            if pattern.search(command):
                return f"这条命令会{why}，执行后无法撤销"
        if _PIPE_TO_SHELL.search(command):
            # 措辞不能再写死"网络上下载的"：泛化后 `cat cmd.txt | sh` 同样命中，
            # 而审批卡上给用户看的就是这句，说的必须是它真的会做的事。
            return "这条命令会把上一步的输出直接当脚本执行"
        return None

    if name == "browser_act":
        action = str((args or {}).get("action") or "").strip().lower() if isinstance(args, dict) else ""
        if action in _MUTATING_BROWSER_ACTS:
            return "这一步会向网站提交内容，可能产生无法撤销的结果"
        if action == "press":
            # 空 text = browser_act 默认按 Enter，同样要判（见 _SUBMIT_KEYS 注释）
            key = str((args or {}).get("text") or "").strip().lower() if isinstance(args, dict) else ""
            if not key or key in _SUBMIT_KEYS:
                return "这一步会在当前焦点上按回车——焦点在表单里时等同于提交，可能产生无法撤销的结果"
        blob = _text_of(args, "element", "text").lower()
        for word in _RISKY_ELEMENT_WORDS:
            if word.lower() in blob:
                return f"这一步要操作的是「{word}」类元素，可能向网站真实提交内容"
        return None

    return None
