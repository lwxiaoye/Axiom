"""连接器 provider 注册表——「再接一个外部应用」的唯一扩展点。

一个 provider 描述三件事：**怎么授权**、**挂什么工具**、**用户要选什么资源**。

两类实现路径：
- `kind="mcp"`：对方有官方远程 MCP Server（GitHub / Notion / Linear …）。工具清单由
  `mcp_client.list_mcp_tools` 直接发现，我们零适配代码，只负责鉴权头与资源白名单。
- `kind="native"`：对方没有 MCP（国内应用大多如此——QQ 邮箱是 IMAP/SMTP + 用户在设置里
  自助生成的**授权码**，既没有 MCP 也不给个人版 OAuth）。这类需要各写一个适配器把能力
  包成工具，授权方式填 `token` 并把 `token_help` 写清「授权码在哪拿」。
  注册一个 native provider 需要补的东西：本文件加一条 ProviderSpec + 一个实现
  `list_tools()/call_tool()/verify()` 的适配模块，其余（加密存储、开关、挂载、白名单闸）
  全部复用，不必改动上层。

授权方式：
- `oauth`：标准授权码流程。点「连接」在新窗口打开对方的登录授权页，授权完浏览器被重定向
  回我们的回调地址换令牌。回调是**浏览器跳转**、不是对方服务器来访问我们，所以内网部署
  同样可用。
- `token`：用户自己粘一串长期凭据（GitHub 的 PAT、国内应用的授权码）。既是没配 OAuth App
  时的兜底，也是 native provider 的常态（QQ 邮箱这类只给授权码、不给个人版 OAuth）。
"""
from dataclasses import dataclass
from typing import Optional

from app.core.config import settings


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    name: str
    # 前端图标标识（前端按此 id 映射到自带图标，不外链第三方 CDN）
    icon: str
    summary: str
    kind: str                       # mcp / native
    auth_kinds: tuple[str, ...]     # ("oauth", "token")
    # 资源作用域：用户连接后要勾选哪一类东西（GitHub=代码库）。空串=无需选择，连上即可用。
    resource_kind: str = ""
    resource_label: str = ""
    resource_hint: str = ""
    # 「用访问令牌连接」这条路的引导：各家拿凭据的地方完全不同，必须逐个写清楚
    token_label: str = "访问令牌"
    token_help: str = ""
    token_link: str = ""
    # 令牌之外还要一个账号标识时填它（IMAP 类必须：登录要「邮箱地址 + 授权码」两样）。
    # 空串 = 只收一个令牌字段，前端不渲染账号输入框。
    account_label: str = ""
    account_placeholder: str = ""
    # 账号字段的格式，驱动前端的本地校验。**必须是显式字段，不能让前端从别处猜**：
    # 前端原先按 `resourceKind === 'mailbox'` 判断要不要校验邮箱格式，而后端实际下发的是
    # `'folder'`（邮件文件夹）——整段校验从来没执行过，界面上却看着像"已经有校验了"，
    # 比没有校验更糟。退而求其次从 placeholder 里找 `@` 也不行：那是文案，改一次示例
    # 就静默失效。取值：""（不校验）/ "email"。
    account_format: str = ""
    # 详情页「配置 X」的去处：对方账号里管理本连接授权的页面。
    # 不能拿 token_link 顶替——那是**生成访问令牌**的页面，对走登录授权连上来的账号是错的。
    config_link: str = ""
    beta: bool = False
    # ---- 连接器详情弹窗（2026-07-29）----
    # summary 是列表行的一句话；详情弹窗要一段完整介绍，说清「连上之后能干什么」。
    description: str = ""
    # 示例提示词：详情弹窗里那四张卡。写成**用户可以直接抄去用**的整句，
    # 不是功能名词罗列——卡片存在的意义是让人立刻知道「这东西怎么用」。
    example_prompts: tuple[str, ...] = ()
    # 详情表格三项。author 是**连接器的提供方**（谁做的这个集成），不是应用厂商。
    # 对方官方提供远程 MCP 的（Canva），集成就是人家做的，author 要写对方名字。
    author: str = "AXIOM 校园智能体"
    homepage: str = ""
    privacy_link: str = ""
    # 开发者文档。参考实现里 Canva 那张详情卡「更多信息」有三个链接（网站/文档/隐私政策），
    # 自研适配的那几家通常只有两个——所以这项可空，前端 v-if 挡掉。
    doc_link: str = ""

    @property
    def detail_description(self) -> str:
        """详情弹窗用的介绍：没单独写就退回 summary，不留空段。"""
        return self.description or self.summary

    @property
    def connector_type_label(self) -> str:
        """详情表格里「连接器类型」那一格的显示文案。

        `mcp` = 对方官方开了远程 MCP，我们零适配直接接（工具清单由对方定义）；
        `native` = 没有 MCP，工具是我们自己包的适配器。
        这个区别对用户是有意义的——MCP 那类的能力边界由厂商决定，出问题也该找厂商。
        """
        return "MCP" if self.kind == "mcp" else "应用"

    @property
    def mcp_url(self) -> str:
        return _MCP_URL_RESOLVERS.get(self.id, lambda: "")()

    def available(self) -> tuple[bool, str]:
        """provider 当前是否可用（缺配置时给出可操作的原因）。"""
        if self.kind == "mcp" and not self.mcp_url:
            return False, "未配置该应用的 MCP 服务地址"
        # 一种可用的授权方式都没有 = 点了「连接」也走不下去。
        # 这条原先不需要：GitHub 声明了 oauth+token，OAuth 没配还能退到令牌，恒有路可走。
        # Gmail/Outlook/Canva 只声明 oauth，缺凭据时就是**零授权方式**，此时若仍报「可用」，
        # 界面会给出一个点下去什么都不发生的连接按钮。
        if not self.auth_available():
            return False, (f"未配置 {self.name} 的登录授权凭据（Client ID / Secret）")
        return True, ""

    def oauth_configured(self) -> bool:
        """本 provider 的 OAuth 应用凭据是否配齐。

        原先这里是 `self.id == "github"` 硬编码；接第二家 OAuth 应用（Gmail/Outlook/Canva）
        时它会静默把新 provider 判成"已配置"，于是点连接直接跳到一个填着空 client_id 的
        授权页 —— 对方回一句看不懂的报错。改成按注册表逐家查。
        """
        pair = _OAUTH_CREDENTIAL_KEYS.get(self.id)
        if not pair:
            return False
        return all(str(getattr(settings, key, "") or "").strip() for key in pair)

    def auth_available(self) -> tuple[str, ...]:
        """实际可用的授权方式：oauth 需要 Client ID + Secret，没配就只剩令牌直连。"""
        kinds = []
        for kind in self.auth_kinds:
            if kind == "oauth" and not self.oauth_configured():
                continue
            kinds.append(kind)
        return tuple(kinds)

    def auth_notice(self) -> str:
        """本 provider 声明支持、但当前环境缺配置而**用不了**的授权方式的说明。

        为什么必须有这个：点「连接」本该跳转对方的登录授权页，缺 OAuth 应用凭据时会
        静默退到「粘访问令牌」——用户看到的是一个完全不同的界面，却没有任何线索说明
        为什么，只会以为跳转登录这个功能根本没做（2026-07-28 真机走查实测到）。
        降级可以，但必须**说出来**。
        """
        declared = set(self.auth_kinds)
        usable = set(self.auth_available())
        if "oauth" in declared and "oauth" not in usable:
            if usable:
                return (f"未配置 {self.name} 登录授权应用（Client ID / Secret），"
                        f"暂时只能用{self.token_label}连接。")
            # 只有 OAuth 一条路的 provider（Gmail / Outlook / Canva）缺凭据时是**彻底不可用**，
            # 不能沿用上面那句"改用令牌"——那条路根本不存在，照着做只会更困惑。
            return (f"未配置 {self.name} 登录授权应用（Client ID / Secret），"
                    f"该连接器暂不可用。请先在管理后台填入凭据。")
        return ""


# MCP 地址走 settings 取值（可运维覆盖），故用惰性解析而非 dataclass 常量
_MCP_URL_RESOLVERS = {
    "github": lambda: (settings.CONNECTOR_GITHUB_MCP_URL or "").strip(),
    "canva": lambda: (settings.CONNECTOR_CANVA_MCP_URL or "").strip(),
}

# 各 provider 的 OAuth 应用凭据配置项名。**新增 OAuth provider 必须在这里登记**，
# 否则 oauth_configured() 恒为 False，连接器会一直显示「未配置」。
#
# 数量不固定：GitHub 要三样——除 Client ID / Secret 外还要 App slug，因为第二段授权
# （跳安装页选仓库）的地址是 github.com/apps/<slug>/installations/new 拼出来的。
# 少了它「登录」这一步照样能成，但「添加代码库」会拼出一个坏地址——连上了却读不到东西，
# 且界面上看不出缺什么。所以 slug 必须和凭据一起算进「配齐」的判据里。
_OAUTH_CREDENTIAL_KEYS: dict[str, tuple[str, ...]] = {
    "github": ("CONNECTOR_GITHUB_CLIENT_ID", "CONNECTOR_GITHUB_CLIENT_SECRET",
               "CONNECTOR_GITHUB_APP_SLUG"),
    "gmail": ("CONNECTOR_GOOGLE_CLIENT_ID", "CONNECTOR_GOOGLE_CLIENT_SECRET"),
    "outlook": ("CONNECTOR_MICROSOFT_CLIENT_ID", "CONNECTOR_MICROSOFT_CLIENT_SECRET"),
    "canva": ("CONNECTOR_CANVA_CLIENT_ID", "CONNECTOR_CANVA_CLIENT_SECRET"),
}


GITHUB = ProviderSpec(
    id="github",
    name="GitHub",
    icon="github",
    summary="让主对话读取你选中的代码库",
    kind="mcp",
    auth_kinds=("oauth", "token"),
    resource_kind="repo",
    resource_label="代码库",
    resource_hint="只有勾选的代码库会被读取；未勾选的仓库调用会被直接拒绝。",
    token_label="访问令牌",
    token_help="在 GitHub → Settings → Developer settings → Personal access tokens 生成，"
               "至少勾选 repo（只读即可）。",
    token_link="https://github.com/settings/tokens",
    # 已授权的 OAuth 应用列表：用户在这里能看到授权范围、也能随时撤销
    config_link="https://github.com/settings/applications",
    description="在主对话里直接访问、搜索你授权的代码库，读取源码与文档、"
                "查看提交与分支、跟踪 issue 和 pull request，并据此写代码、出方案或做评审。",
    example_prompts=(
        "总结这个仓库最近的提交，说清都改了什么、有没有值得注意的风险。",
        "找出仓库里长期没有处理的 issue，按影响面排个序并给出下一步建议。",
        "读一下这个项目的结构和关键文件，给新同事写一份上手说明。",
        "看看当前打开的 pull request，逐个说明改动内容和评审状态。",
    ),
    homepage="https://github.com",
    privacy_link="https://docs.github.com/site-policy/privacy-policies/github-privacy-statement",
)


QQMAIL = ProviderSpec(
    id="qqmail",
    name="QQ 邮箱",
    icon="qqmail",
    summary="让主对话读取、整理并起草回复你的 QQ 邮件",
    kind="native",
    # 腾讯不给个人邮箱 OAuth（QQ 互联那套只能拿到 openid/昵称头像，碰不到邮件），
    # 唯一通道是 IMAP/SMTP + 用户自助生成的授权码，所以这里没有 oauth 这一项。
    auth_kinds=("token",),
    # 邮箱**不做文件夹级白名单**（2026-07-29 用户拍板，对标参考实现）。
    #
    # 原先 resource_kind="folder"，于是套了 GitHub 那套「先勾资源才生效」的模型：
    # 用户授权码填对、凭据验证通过、连接显示成功，界面却是「未勾选文件夹，尚未生效」，
    # 工具一个都不挂——**连上了却用不了的死胡同**，而缺的那一步藏在飞出面板里，
    # 不悬停根本看不见。仓库要挑（一个账号几十上百个），邮箱不用：用户连的就是他自己
    # 那一个邮箱，"连上了但读不到"不符合任何人的预期。
    #
    # 去掉后 load_active_bindings 里 `if provider.resource_kind and not
    # _parse_resources(binding): continue` 对邮箱不再生效；而 imapmail.call_tool 是
    # `if allowed and folder not in allowed`，**空列表 = 不限制**，所以效果正好是
    # "全部文件夹可读"而不是"全部拒绝"。（查过才改。）
    #
    # ⚠️ OUTLOOK 目前仍是 resource_kind="folder"，会撞同一个死胡同；它由另一个会话
    # 负责，这里只按用户要求改 QQ 邮箱，没有顺手连坐。
    resource_kind="",
    # 断开连接的确认文案在用它（「主对话将无法再读取{resource_label}」），
    # 所以给个通顺的词而不是清空。
    resource_label="邮件",
    resource_hint="",
    token_label="授权码",
    # ⚠️ 「按页面提示完成验证」这句是**故意不写死短信**的：腾讯的验证弹窗默认是
    # 「发短信到指定号码」，但下面还有「选择其他方式验证」下拉（QQ 密码 / 手机 QQ 扫码），
    # 而**微信绑定的邮箱账号走的是微信扫码、根本没有短信这一步**。写成"必须发短信"的话，
    # 微信账号用户会卡住——而且卡在最后一步，前面找入口、开服务全都做完了。
    # 路径与服务名以**用户实测截图**为准（2026-07-29 用户纠正）：左侧导航只有
    # 「账号设置 / 安全设置 / 设备管理」三项，**没有「账号与安全」这一层**；
    # 服务的正式名是「POP3/IMAP/SMTP/Exchange/CardDAV 服务」而不是「IMAP/SMTP 服务」，
    # 且多数账号本来就是已开启状态——直接点【生成授权码】即可，不必先"开启"。
    # 腾讯官方文档正文与它自己的截图矛盾，两者冲突时一律以截图/实际页面为准。
    token_help="登录 mail.qq.com → 右上角齿轮【设置】→ 左侧【安全设置】，"
               "找到「POP3/IMAP/SMTP/Exchange/CardDAV 服务」（未开启就先开启），"
               "点【生成授权码】并按页面提示完成验证，"
               "复制 16 位授权码（只显示一次、可生成多个）",
    token_link="https://mail.qq.com",
    account_label="邮箱地址",
    account_placeholder="you@qq.com",
    account_format="email",
    description="在主对话里查看收件箱、按条件搜索邮件、读取正文与附件，并起草回复。"
                "附件可以直接存进「我的文件」，接着做表格分析、OCR 或生成文档——"
                "「收到邮件 → 打开附件 → 整理成材料」这条链能在一句话里跑完。",
    example_prompts=(
        "看看我今天收到的邮件，挑出需要我回复的，按紧急程度排个序。",
        "找出上个月所有账单或付款确认邮件，整理成发件人、金额、日期的表格。",
        "把这封邮件的附件存到我的文件里，然后帮我分析里面的数据。",
        "找出本周还没回复的邮件，逐封起草一段简短得体的回复。",
    ),
    homepage="https://mail.qq.com",
    doc_link="https://service.mail.qq.com/",
    privacy_link="https://privacy.qq.com/",
)


GMAIL = ProviderSpec(
    id="gmail",
    name="Gmail",
    icon="gmail",
    summary="让主对话读取、搜索、整理并起草回复你的 Gmail",
    kind="native",
    # Google 给个人邮箱开了正经 OAuth，所以这条走登录授权，不走粘令牌
    auth_kinds=("oauth",),
    # 不做标签级勾选（2026-07-29 用户拍板，对齐参考实现）：飞出面板列的是**账户**，
    # 开关一开即读全部邮件。`resource_kind` 留空是关键——`load_active_bindings` 里有
    # 一道 `if provider.resource_kind and not _parse_resources(binding): continue`，
    # 不清空的话就是「授权成功、开关能开、工具一个都不挂」的死胡同，而界面上
    # 已经没有可勾的东西了，用户无法自救。
    #
    # ⚠️ 这一步的前置是 gmail.py 的空名单语义已经从 fail-closed 改成
    # 「入参空=不限制、入参非空但解析全失败=仍拒绝」。顺序反了同样是死胡同，
    # 只是报错换成「请先到连接器里勾选标签」。
    resource_kind="",
    resource_label="邮件",
    config_link="https://myaccount.google.com/permissions",
    description="在主对话里访问和搜索 Gmail（用的是 Gmail 自己的搜索语法，"
                "不是笨条件拼接）、按标签整理、读取正文与附件，并把回复写成草稿"
                "放进你的草稿箱——发送键始终在你手上。",
    example_prompts=(
        "找出本周所有需要回复的未读邮件，并起草清晰简短的回复。",
        "搜索近期关于活动计划或项目进展的邮件，总结要点和后续步骤。",
        "看看过去两周的邮件，生成一份按优先级排列的待办清单，带截止日期。",
        "找出上个月收到的所有账单或付款确认邮件，整理发件人、金额和日期。",
    ),
    homepage="https://mail.google.com",
    doc_link="https://developers.google.com/gmail/api",
    privacy_link="https://policies.google.com/privacy",
)


OUTLOOK = ProviderSpec(
    id="outlook",
    name="Outlook Mail",
    icon="outlook",
    summary="让主对话读取、搜索、整理并起草回复你的 Outlook 邮件",
    kind="native",
    # 微软已停用个人 outlook.com 账号的 IMAP basic auth，**只能**走 OAuth，没有令牌兜底
    auth_kinds=("oauth",),
    resource_kind="folder",
    resource_label="文件夹",
    resource_hint="只有勾选的文件夹会被读取；未勾选的文件夹调用会被直接拒绝。",
    config_link="https://account.live.com/consent/Manage",
    description="在主对话里访问和搜索 Outlook 邮件、按文件夹整理、读取正文与附件，"
                "并起草回复。个人账号和工作/学校账号都支持。",
    example_prompts=(
        "在我的 Outlook 收件箱中搜索本月的重要邮件，提供关键信息摘要。",
        "找出过去一周未回复的邮件，起草礼貌的跟进回复。",
        "收集与我即将安排相关的所有邮件，制作清晰的总结以便跟进。",
        "审查近期的各类邮件，按类别和下一步行动整理关键信息。",
    ),
    homepage="https://outlook.com",
    privacy_link="https://privacy.microsoft.com/privacystatement",
)


CANVA = ProviderSpec(
    id="canva",
    name="Canva",
    icon="canva",
    summary="搜索、导入、整理并导出你的 Canva 项目",
    # 唯一一个 kind="mcp" 的新成员：Canva 官方开了远程 MCP，工具由他们定义，
    # 我们零适配代码，只负责鉴权头。详情弹窗里「作者」因此要写 Canva 而不是我们。
    kind="mcp",
    auth_kinds=("oauth",),
    config_link="https://www.canva.com/settings/your-apps",
    description="搜索、导入、自动填写、整理并导出你的 Canva 项目，以简化创意工作。"
                "可以把主对话里生成的内容直接做成 Canva 设计，也可以把已有设计取回来"
                "继续加工。",
    example_prompts=(
        "制作一份色彩丰富的演示文稿，总结我的项目或学习笔记的要点，通过 Canva 完成。",
        "设计一张吸引眼球的社交图片，分享即将举行的活动或聚会的详情，通过 Canva 完成。",
        "将与我的家庭、爱好或学习相关的 Canva 设计整理成一个整洁的收藏并进行浏览。",
        "制作一页简洁的传单，清晰说明新的想法、食谱或社区公告，通过 Canva 完成。",
    ),
    author="Canva",
    homepage="https://www.canva.com",
    doc_link="https://www.canva.dev/docs/connect/",
    privacy_link="https://www.canva.com/policies/privacy-policy/",
)


# 顺序即前端列表顺序：先已成型的，再邮件三家。
#
# ⚠️ CANVA 定义留着但**不上架**（2026-07-29 用户拍板暂缓）。技术上它最省事——官方远程
# MCP 现成、我们的 MCP 客户端直接能用——但它卡在一道我们控制不了的闸：Canva 要求
# 回调地址先进他们的 allowlist，而进 allowlist 要填 Waitlist 表单排人工审核，官方
# 没有时限承诺也没有通过承诺。退一步走 Connect API 的 Private 集成又要求团队在
# Canva Enterprise 套餐上。等拿到准入，把 CANVA 加回下面这个元组即可，别的都不用动。
# ⚠️ OUTLOOK 同样留定义、不上架（2026-07-29 用户拍板去掉）。它卡的**不是代码**——
# 适配器写完测完、注册表和配置项都在位，唯一缺的是 client_id / secret。而拿不到凭据
# 的原因是账号类型：个人 Microsoft 账号不属于任何目录，而微软已弃用「在目录外部创建
# 应用程序」。三条路实测都堵死：直接注册（弹窗只有取消）、M365 开发者计划沙盒
# （不符合资格）、从 Entra 门户建租户（401）。剩下要么注册 Azure 免费订阅（要绑卡），
# 要么用一个自带租户的账号（如学校的 M365）。
# 哪天有了凭据：填进 override 的 CONNECTOR_MICROSOFT_* 两项 + 把 OUTLOOK 加回下面
# 这个元组，其余一行都不用改。
_PROVIDERS: tuple[ProviderSpec, ...] = (GITHUB, QQMAIL, GMAIL)

_BY_ID = {p.id: p for p in _PROVIDERS}


def list_providers() -> tuple[ProviderSpec, ...]:
    return _PROVIDERS


def get_provider(provider_id: str) -> Optional[ProviderSpec]:
    return _BY_ID.get(str(provider_id or "").strip().lower())
