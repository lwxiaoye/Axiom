"""路径寻址文件工具定向测试（2026-07-27，批 2）。

重点：
- **路径三种写法都要接住**（`a.txt` / `files/a.txt` / `/workspace/files/a.txt`）——就是 bash 里
  看到的同一批文件；接不住模型每次都得猜。
- **`edit_file` 的唯一匹配校验**：0 次和多次都必须明确报错。`sed -i` 的静默不匹配是最坏的
  形态——模型以为改了，其实什么都没发生（借鉴 pi 的 edit.ts）。
- **同名工具不能并存**：开关打开后旧的 file_id 版 read_file/edit_file/update_file/create_file/
  list_files 必须消失，否则模型脑子里又是两套寻址（这次改造就是要消掉它）。
- 写工具必须进授权门禁（Harness：工具集合本身即授权边界）。
"""
import pytest

from app.core.config import settings
from app.services.chat.tools import build_tools
from app.services.chat.tools.base import ToolSoftError
from app.services.chat.tools.paths import build_path_tools, normalize_path
from app.services.agent_harness.contracts import AgentMode, RunPhase, RunSnapshot
from app.services.agent_harness.policy import HarnessPolicy


# ---------- 路径归一化 ----------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("a.txt", "a.txt"),
        ("files/a.txt", "a.txt"),
        ("/workspace/files/a.txt", "a.txt"),
        ("/workspace/files/sub/a.txt", "sub_a.txt"),      # 子目录压平（我的文件是平铺模型）
        ("./a.txt", "a.txt"),
        ("  spaced.md  ", "spaced.md"),
        ("\\workspace\\files\\b.txt", "b.txt"),           # Windows 风格反斜杠
    ],
)
def test_normalize_path_accepts_all_writings(raw, expected):
    assert normalize_path(raw) == expected


@pytest.mark.parametrize("bad", ["", "   ", "/", "..", "./../"])
def test_normalize_path_rejects_empty_and_traversal(bad):
    with pytest.raises(ToolSoftError):
        normalize_path(bad)


# ---------- 工具替换与门禁 ----------

@pytest.mark.asyncio
async def test_path_tools_replaced_the_file_id_tools():
    """路径寻址是唯一形态（2026-07-29）：file_id 版那批名字一个都不该在工具集里。

    原先这条叫 `..._when_sync_on` 并 monkeypatch SANDBOX_WORKSPACE_SYNC=True；开关随旧
    execute_in_sandbox 工具族下线后那行 monkeypatch 是有害的——属性已不存在，`raising=False` 会凭空
    造一个字段，teardown 里 pydantic 的 __delattr__ 抛 AttributeError，**同一个 monkeypatch
    栈里更早注册的补丁因此全都不回滚**（实测污染了 test_workspace_sync 的 15 条）。
    """
    tools = await build_tools(
        token="t", knowledge_ids=None, web_enabled=True, user_id="u1", thread_id="th",
        newapi_key="k", run_id="r1", user_message="处理文件",
        turn_intent="execution", action_authority="mutate",
    )
    names = set(t.name for t in tools)
    assert {"glob", "read_file", "write_file"} <= names
    # list_files 被 glob 取代
    assert "list_files" not in names
    # 2026-07-27 收口：遗留 file_id 版五个全部退休（那两条原位修改语义已移植进 paths.py）
    assert "edit_file" in names, "edit_file 现在是路径版"
    assert "update_file" not in names
    assert "create_file" not in names


# 这里原先有一条 test_old_tools_survive_when_sync_off（断言"开关关闭时 list_files /
# update_file / create_file 仍然注册，随时可退"）。2026-07-29 用户拍板只保留 bash，
# 回退形态与那三个工具一起删除，"随时可退"这个前提不再成立，用例随之删除——
# 反向门禁改由 test_legacy_tools_gone.py 承担（断言它们在任何组合下都不注册）。


@pytest.mark.asyncio
async def test_write_tools_absent_for_readonly_turn():
    tools = await build_tools(
        token="t", knowledge_ids=None, web_enabled=True, user_id="u1", thread_id="th",
        newapi_key="k", run_id="r1", user_message="看看",
        turn_intent="conversation", action_authority="inspect",
    )
    run = RunSnapshot(
        run_id="r1", thread_id="th", user_id="u1",
        agent_mode=AgentMode.STANDARD, phase=RunPhase.EXECUTING,
        capability_scope="inspect", state_version=0, goal_revision=0,
        plan_version=0, event_cursor=0,
        execution_profile={"id": "interactive"},
    )
    policy = HarnessPolicy()
    visible = [tool for tool in tools if policy.evaluate(tool.spec, run).allowed]
    names = {tool.name for tool in visible}
    assert not ({"write_file", "edit_file"} & names), "只读轮次不能出现用户文件写工具"
    assert {"glob", "read_file", "bash"} <= names, "读取与临时执行工具应该还在"
    bash = next(tool for tool in visible if tool.name == "bash")
    assert bash.spec.effect_scope.value == "scratch"
    assert "artifact_producer" not in bash.spec.semantic_tags


def test_readonly_flags():
    tools = {t.name: t for t in build_path_tools(user_id="u1")}
    assert tools["glob"].readonly and tools["glob"].parallel_safe
    assert tools["read_file"].readonly and tools["read_file"].parallel_safe
    for name in ("write_file", "download_url"):
        assert tools[name].readonly is False
        assert tools[name].parallel_safe is False
    assert all(
        {profile.value for profile in tool.spec.allowed_execution_profiles} == {"interactive"}
        for tool in tools.values()
    )


# ---------- 读写编辑（打桩 user_file_service） ----------

def _stub_fs(monkeypatch, files: dict):
    """files: {filename: bytes}。返回一个记录 overwrite/save 调用的 dict。"""
    calls = {"overwrite": [], "save": []}
    ids = {name: f"id-{i}" for i, name in enumerate(files)}

    async def list_files(_uid, _folder=None):
        return {"files": [
            {"id": ids[n], "filename": n, "size": len(b), "createdAt": "2026-07-27T00:00:00"}
            for n, b in files.items()
        ]}

    async def read_bytes(_uid, fid):
        for n, i in ids.items():
            if i == fid:
                return (None, files[n])
        raise RuntimeError("not found")

    async def overwrite_file(_uid, fid, data, **kw):
        for n, i in ids.items():
            if i == fid:
                files[n] = bytes(data)
        calls["overwrite"].append((fid, bytes(data)))
        return {}

    async def update_file_content(_uid, fid, content, **kw):
        # path 版写入走带 sha256 乐观锁的 update_file_content（不再是 overwrite_file）
        for n, i in ids.items():
            if i == fid:
                files[n] = content.encode() if isinstance(content, str) else bytes(content)
        calls["overwrite"].append((fid, files[[n for n,i in ids.items() if i==fid][0]]))
        return {}

    async def save_file(_uid, name, data, **kw):
        files[name] = bytes(data)
        ids[name] = f"id-new-{name}"
        calls["save"].append((name, bytes(data)))
        return {"id": ids[name]}

    import app.services.files.user_file_service as ufs
    for fn in (list_files, read_bytes, overwrite_file, save_file, update_file_content):
        monkeypatch.setattr(ufs, fn.__name__, fn)
    return calls


@pytest.mark.asyncio
async def test_runtime_path_tools_cannot_enumerate_other_threads_or_unselected_files(monkeypatch):
    rows = [
        {"id": "selected", "filename": "picked.pdf", "size": 1},
        {"id": "local", "filename": "draft.md", "size": 1, "threadId": "t1"},
        {"id": "hidden", "filename": "other.pdf", "size": 1, "threadId": "t2"},
    ]

    async def list_files(_uid, _folder=None):
        return {"files": rows}

    import app.services.files.user_file_service as ufs
    monkeypatch.setattr(ufs, "list_files", list_files)
    tools = {t.name: t for t in build_path_tools(
        user_id="u1",
        thread_id="t1",
        attachments=[{"file_id": "selected", "filename": "picked.pdf"}],
        scope_to_thread=True,
    )}
    listing = (await tools["glob"].execute({"pattern": "*"})).model_content
    assert "picked.pdf" in listing and "draft.md" in listing
    assert "other.pdf" not in listing


@pytest.mark.asyncio
async def test_read_missing_file_is_soft_error(monkeypatch):
    _stub_fs(monkeypatch, {})
    tools = {t.name: t for t in build_path_tools(user_id="u1")}
    with pytest.raises(ToolSoftError) as e:
        await tools["read_file"].execute({"path": "nope.txt"})
    assert "glob" in str(e.value), "报错要告诉模型下一步怎么办，而不是只说不存在"


@pytest.mark.asyncio
async def test_read_line_range(monkeypatch):
    _stub_fs(monkeypatch, {"a.txt": b"l1\nl2\nl3\nl4\n"})
    tools = {t.name: t for t in build_path_tools(user_id="u1")}
    out = (await tools["read_file"].execute({"path": "a.txt", "offset": 1, "limit": 2})).model_content
    assert "共 4 行" in out and "第 2~3 行" in out
    assert "l2" in out and "l3" in out and "l4" not in out


@pytest.mark.asyncio
async def test_edit_unique_match_succeeds(monkeypatch):
    files = {"a.txt": b"alpha\nbeta\ngamma\n"}
    _stub_fs(monkeypatch, files)
    tools = {t.name: t for t in build_path_tools(user_id="u1")}
    out = (await tools["edit_file"].execute(
        {"path": "a.txt", "old_string": "beta", "new_string": "BETA"})).model_content
    assert "已修改" in out
    assert files["a.txt"] == b"alpha\nBETA\ngamma\n"


@pytest.mark.asyncio
async def test_edit_no_match_errors_and_does_not_write(monkeypatch):
    files = {"a.txt": b"alpha\n"}
    calls = _stub_fs(monkeypatch, files)
    tools = {t.name: t for t in build_path_tools(user_id="u1")}
    with pytest.raises(ToolSoftError) as e:
        await tools["edit_file"].execute({"path": "a.txt", "old_string": "zzz", "new_string": "x"})
    assert "找不到" in str(e.value)
    assert calls["overwrite"] == [], "匹配不到时绝不能写回"
    assert files["a.txt"] == b"alpha\n"


@pytest.mark.asyncio
async def test_edit_multiple_matches_errors(monkeypatch):
    """静默改第一处是最坏的行为——必须要求模型加长上下文。"""
    files = {"a.txt": b"x\nx\n"}
    calls = _stub_fs(monkeypatch, files)
    tools = {t.name: t for t in build_path_tools(user_id="u1")}
    with pytest.raises(ToolSoftError) as e:
        await tools["edit_file"].execute({"path": "a.txt", "old_string": "x", "new_string": "y"})
    assert "2 次" in str(e.value)
    assert calls["overwrite"] == []


@pytest.mark.asyncio
async def test_edit_rejects_empty_old_and_noop(monkeypatch):
    _stub_fs(monkeypatch, {"a.txt": b"a\n"})
    tools = {t.name: t for t in build_path_tools(user_id="u1")}
    with pytest.raises(ToolSoftError):
        await tools["edit_file"].execute({"path": "a.txt", "old_string": "", "new_string": "x"})
    with pytest.raises(ToolSoftError):
        await tools["edit_file"].execute({"path": "a.txt", "old_string": "a", "new_string": "a"})


@pytest.mark.asyncio
async def test_edit_rejects_binary_formats(monkeypatch):
    _stub_fs(monkeypatch, {"deck.pptx": b"PK\x03\x04"})
    tools = {t.name: t for t in build_path_tools(user_id="u1")}
    with pytest.raises(ToolSoftError) as e:
        await tools["edit_file"].execute(
            {"path": "deck.pptx", "old_string": "a", "new_string": "b"})
    assert "bash" in str(e.value), "要指路到能做这件事的工具"


@pytest.mark.asyncio
async def test_write_creates_then_overwrites(monkeypatch):
    files: dict = {}
    calls = _stub_fs(monkeypatch, files)
    tools = {t.name: t for t in build_path_tools(user_id="u1")}
    out1 = (await tools["write_file"].execute({"path": "n.md", "content": "# a\n"})).model_content
    assert "已新建" in out1 and len(calls["save"]) == 1
    out2 = (await tools["write_file"].execute({"path": "n.md", "content": "# a\n# b\n"})).model_content
    assert "已覆写" in out2 and len(calls["overwrite"]) == 1
    assert files["n.md"] == b"# a\n# b\n"


@pytest.mark.asyncio
async def test_write_requires_content(monkeypatch):
    """缺 content 时不能写空文件把原文件清掉。"""
    files = {"a.txt": b"important\n"}
    _stub_fs(monkeypatch, files)
    tools = {t.name: t for t in build_path_tools(user_id="u1")}
    with pytest.raises(ToolSoftError):
        await tools["write_file"].execute({"path": "a.txt"})
    assert files["a.txt"] == b"important\n"


@pytest.mark.asyncio
async def test_glob_empty_and_matching(monkeypatch):
    _stub_fs(monkeypatch, {"a.txt": b"x", "b.docx": b"y"})
    tools = {t.name: t for t in build_path_tools(user_id="u1")}
    out = (await tools["glob"].execute({"pattern": "*.txt"})).model_content
    assert "a.txt" in out and "b.docx" not in out
    none = (await tools["glob"].execute({"pattern": "*.pdf"})).model_content
    assert "没有匹配" in none and "2 个文件" in none, "空结果要说清文件区其实有东西"


# ---------- 编解码与可编辑格式（2026-07-27 审计） ----------

def _fs(monkeypatch, files: dict):
    """最小文件区替身：{name: bytes}。返回 calls 记录写入。"""
    import app.services.files.user_file_service as ufs
    rows = [{"id": f"id-{n}", "filename": n, "size": len(b), "createdAt": "2026-07-27"}
            for n, b in files.items()]
    calls: dict = {"update": [], "save": []}

    async def list_files(_uid, _folder=None):
        return {"files": rows}

    async def read_bytes(_uid, fid):
        for n, b in files.items():
            if fid == f"id-{n}":
                return ({"id": fid, "filename": n}, b)
        raise RuntimeError("no such file")

    async def update_file_content(_uid, fid, text, **kw):
        calls["update"].append((fid, text))
        return {"id": fid, "filename": fid.replace("id-", ""), "size": len(text)}

    async def save_file(_uid, name, data, **kw):
        calls["save"].append((name, data))
        return {"id": f"id-{name}", "filename": name, "size": len(data)}

    monkeypatch.setattr(ufs, "list_files", list_files)
    monkeypatch.setattr(ufs, "read_bytes", read_bytes)
    monkeypatch.setattr(ufs, "update_file_content", update_file_content)
    monkeypatch.setattr(ufs, "save_file", save_file)
    return calls


def _tool(tools, name):
    return next(t for t in tools if t.name == name)


@pytest.mark.asyncio
async def test_edit_matches_crlf_file_with_lf_old_string(monkeypatch):
    """read_file 把 CRLF 归一成 LF 展示，模型照抄给出的就是 LF —— 必须能对上。

    否则 Windows 换行的 txt/csv/md（记事本、导出文件的常态）永远改不动：模型刚读过原文，
    会照着同样的内容反复重试直到轮次耗尽。
    """
    calls = _fs(monkeypatch, {"win.txt": b"line1\r\nline2\r\nline3\r\n"})
    tools = build_path_tools(user_id="u1")
    out = (await _tool(tools, "edit_file").execute(
        {"path": "win.txt", "old_string": "line1\nline2", "new_string": "LINE1\nLINE2"})).model_content
    assert "已修改" in out
    written = calls["update"][0][1]
    assert "LINE1" in written and "LINE2" in written
    assert "\r\n" in written, "不该擅自把整份文件的换行符改掉——那是用户没要求的全文改动"


@pytest.mark.asyncio
async def test_edit_refuses_non_utf8_instead_of_destroying_it(monkeypatch):
    """GBK 文件用 errors='replace' 解码后写回，一次调用就把整份文件的中文毁掉。

    旧实现是严格解码 + 明确拒绝，迁移时丢了。这里守住"宁可拒绝也不破坏"。
    """
    calls = _fs(monkeypatch, {"gbk.txt": "第一行 keep\n第二行\n".encode("gbk")})
    tools = build_path_tools(user_id="u1")
    with pytest.raises(ToolSoftError) as e:
        await _tool(tools, "edit_file").execute(
            {"path": "gbk.txt", "old_string": " keep", "new_string": " KEEP"})
    assert "UTF-8" in str(e.value) and "bash" in str(e.value)
    assert calls["update"] == [], "拒绝时一个字节都不能写"


@pytest.mark.asyncio
async def test_csv_is_text_editable(monkeypatch):
    """.csv 被 _PARSED_EXTS 误判成"二进制/富格式"而拒绝精确替换——事实错误。"""
    _fs(monkeypatch, {"data.csv": b"a,b\n1,2\n"})
    tools = build_path_tools(user_id="u1")
    out = (await _tool(tools, "edit_file").execute(
        {"path": "data.csv", "old_string": "1,2", "new_string": "3,4"})).model_content
    assert "已修改" in out


@pytest.mark.asyncio
async def test_read_binary_refuses_instead_of_dumping_mojibake(monkeypatch):
    """download_url 的主打用法就是抓归档；当文本读会灌 5000+ 字符乱码进上下文。"""
    _fs(monkeypatch, {"repo.bin": b"PK\x03\x04\x00\x00binary\x00stuff"})
    tools = build_path_tools(user_id="u1")
    with pytest.raises(ToolSoftError) as e:
        await _tool(tools, "read_file").execute({"path": "repo.bin"})
    assert "二进制" in str(e.value)


@pytest.mark.asyncio
async def test_read_offset_past_eof_is_explicit(monkeypatch):
    """原先静默回空正文 + "第 100~99 行"，模型很可能判成"文件是空的"。"""
    _fs(monkeypatch, {"a.txt": b"1\n2\n3\n"})
    tools = build_path_tools(user_id="u1")
    with pytest.raises(ToolSoftError) as e:
        await _tool(tools, "read_file").execute({"path": "a.txt", "offset": 99})
    assert "越过文件末尾" in str(e.value)


@pytest.mark.asyncio
async def test_read_bad_limit_is_soft_error_not_crash(monkeypatch):
    """limit="abc" 原先抛 ValueError（硬失败），模型只看到"工具执行失败"。"""
    _fs(monkeypatch, {"a.txt": b"1\n2\n3\n"})
    tools = build_path_tools(user_id="u1")
    out = (await _tool(tools, "read_file").execute({"path": "a.txt", "limit": "abc"})).model_content
    assert "共 3 行" in out


@pytest.mark.asyncio
async def test_edit_missing_new_string_is_not_a_delete(monkeypatch):
    """漏传 new_string 被静默当成删除，是最难发现的破坏形态。"""
    calls = _fs(monkeypatch, {"a.txt": b"keep this line\n"})
    tools = build_path_tools(user_id="u1")
    with pytest.raises(ToolSoftError) as e:
        await _tool(tools, "edit_file").execute({"path": "a.txt", "old_string": "keep "})
    assert "new_string" in str(e.value)
    assert calls["update"] == []


def test_download_filename_drops_query_string():
    """签名/令牌随查询串落进「我的文件」列表和数据库，且扩展名被毁。"""
    from app.services.chat.tools.paths import _name_from_url

    assert _name_from_url("https://cdn.x.com/f.zip?sig=AKIA_SECRET&t=1") == "f.zip"
    assert _name_from_url("https://x.com/a/report.pdf#page=3") == "report.pdf"


# ---------- 沙箱路径不许被压平成用户文件（2026-07-27 真机事故） ----------

@pytest.mark.parametrize("sandbox_path", [
    "/workspace/tmp/build.py",
    "/workspace/tmp/x/y.py",
    "/workspace/skills/ppt-studio/SKILL.md",
    "/workspace/outputs/deck.pptx",
    "workspace/tmp/e.py",
])
def test_sandbox_paths_are_rejected_not_flattened(sandbox_path):
    """真机：模型按 SKILL.md 把生成器脚本写去 /workspace/tmp，结果用户「我的文件」里
    多出一个 `workspace_tmp_build.py`，而沙箱里那个路径依然是空的。

    模型不是乱来——/workspace/tmp 正是平台自己在工具描述、persist_notice() 和系统提示词里
    让它写的地方，它只是选了 write_file 而不是 bash。压平等于把一个**类别错误**静默转成脏
    数据（.py 黑名单管的是沙箱→文件区那条同步路，write_file 根本不过那道闸）。
    """
    from app.services.chat.tools.paths import normalize_path

    with pytest.raises(ToolSoftError) as e:
        normalize_path(sandbox_path)
    msg = str(e.value)
    assert "沙箱" in msg and "bash" in msg, "报错必须说清「沙箱路径用 bash」，否则模型只会换个写法再撞"


@pytest.mark.parametrize("ok_path,expected", [
    ("/workspace/files/a.pptx", "a.pptx"),      # files/ 是唯一同一批文件的地方
    ("/workspace/files/sub/b.md", "sub_b.md"),  # 子目录仍压平（「我的文件」是平铺模型）
    ("files/c.md", "c.md"),
    ("d.txt", "d.txt"),
])
def test_files_prefix_and_relative_paths_still_work(ok_path, expected):
    """收紧不能把三种正常写法一起干掉——模型每次都得猜就更糟。"""
    from app.services.chat.tools.paths import normalize_path

    assert normalize_path(ok_path) == expected


@pytest.mark.parametrize("tricky", ["/workspace/filesa.txt", "/workspace/files-backup/x.md"])
def test_prefix_strip_requires_separator(tricky):
    """前缀剥离不要求分隔符 = 静默指错文件。

    `/workspace/filesa.txt` 曾被剥成 `a.txt`（一个**完全不同**的文件），
    `/workspace/files-backup/x.md` 被剥成 `-backup_x.md`（垃圾名）。
    """
    from app.services.chat.tools.paths import normalize_path

    with pytest.raises(ToolSoftError):
        normalize_path(tricky)


# ---------- download_url：上限、错误归类、内容校验（2026-07-27 批） ----------

def _stub_download(monkeypatch, *, body: bytes = b"", headers: dict = None,
                   status: int = 200, forbid_read: bool = False):
    """把 download_url 的出网段整段打桩（SSRF 校验 + httpx 流式响应）。

    forbid_read=True：body 被读到就炸——用来断言"超限时一个字节都没下"。
    """
    import httpx

    from app.services.chat.tools import image_fetch

    async def _allow(_url):
        return None

    monkeypatch.setattr(image_fetch, "_url_allowed", _allow)

    class _Resp:
        status_code = status
        url = "https://cdn.example.com/asset"
        def __init__(self):
            self.headers = headers or {}

        async def aiter_bytes(self):
            assert not forbid_read, "Content-Length 已说明超限，不该再读一个字节"
            yield body

    class _Stream:
        async def __aenter__(self):
            return _Resp()

        async def __aexit__(self, *_a):
            return False

    class _Client:
        def __init__(self, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        def stream(self, _method, _url, headers=None):
            return _Stream()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)


def test_download_limit_is_pinned_to_the_persist_limit(monkeypatch):
    """64MB 的下载上限对着 15MB 的落库上限 = 承诺永远兑现不了。

    工具描述教模型"先 download_url 取仓库归档"，一个 40MB 的 tar.gz 会老老实实下完
    40MB，再在 save_file 里撞 413 —— 白等、白占内存，最后只给模型一句通用「工具执行失败」。
    """
    from app.services.chat.tools.paths import _MAX_DOWNLOAD_BYTES, _download_limit

    monkeypatch.setattr(settings, "USER_FILES_MAX_SIZE_MB", 15, raising=False)
    assert _download_limit() == 15 * 1024 * 1024
    assert _download_limit() <= _MAX_DOWNLOAD_BYTES, "绝对硬顶仍在，配置调大也不能吃穿进程"


@pytest.mark.asyncio
async def test_download_stops_before_reading_when_content_length_too_big(monkeypatch):
    calls = _stub_fs(monkeypatch, {})
    monkeypatch.setattr(settings, "USER_FILES_MAX_SIZE_MB", 1, raising=False)
    _stub_download(monkeypatch, headers={"content-length": str(9 * 1024 * 1024)},
                   forbid_read=True)
    tools = {t.name: t for t in build_path_tools(user_id="u1")}
    with pytest.raises(ToolSoftError) as e:
        await tools["download_url"].execute({"url": "https://x.com/big.tar.gz"})
    msg = str(e.value)
    assert "1MB" in msg and "没有下载" in msg
    assert "raw 直链" in msg or "子目录" in msg, "报错要给能照做的下一步，不是只说失败"
    assert calls["save"] == []


@pytest.mark.asyncio
async def test_download_still_stops_when_content_length_lies(monkeypatch):
    """头可能缺失或撒谎，流式那道闸不能省——两道闸是先后不是重复。"""
    calls = _stub_fs(monkeypatch, {})
    monkeypatch.setattr(settings, "USER_FILES_MAX_SIZE_MB", 1, raising=False)
    _stub_download(monkeypatch, body=b"x" * (2 * 1024 * 1024))
    tools = {t.name: t for t in build_path_tools(user_id="u1")}
    with pytest.raises(ToolSoftError) as e:
        await tools["download_url"].execute({"url": "https://x.com/big.bin"})
    assert "超过单文件上限" in str(e.value)
    assert calls["save"] == []


@pytest.mark.asyncio
async def test_download_persist_failure_is_soft_error_not_generic_crash(monkeypatch):
    """save_file 的 UserFileError 没被包 → 冒到循环层变成一句「工具执行失败」，
    模型看不出是大小还是配额，只会原样重试同一个地址（与 _atomic_write 的口径也不一致）。
    """
    import app.services.files.user_file_service as ufs
    _stub_fs(monkeypatch, {})
    _stub_download(monkeypatch, body=b"hello")

    async def boom(*_a, **_kw):
        raise ufs.UserFileError("文件数已达上限", status_code=413)

    monkeypatch.setattr(ufs, "save_file", boom)
    tools = {t.name: t for t in build_path_tools(user_id="u1")}
    with pytest.raises(ToolSoftError) as e:
        await tools["download_url"].execute({"url": "https://x.com/a.txt"})
    msg = str(e.value)
    assert "文件数已达上限" in msg, "要把真实原因带给模型"
    assert "重试同一个地址不会有不同结果" in msg


@pytest.mark.asyncio
async def test_download_rejects_html_pretending_to_be_an_image(monkeypatch):
    """魔数校验在 execute_in_sandbox.fetch_urls 退休时丢了（image_fetch._sniff_ext 一直有）。

    系统提示词是**命令**模型用 download_url 给 PPT 配图的，而防盗链页/403 页会带着 200
    和一个 .jpg 结尾的 URL 回来——不校验就是把一段 HTML 存成 cover.jpg 再嵌进 PPT。
    """
    calls = _stub_fs(monkeypatch, {})
    _stub_download(monkeypatch, body=b"<!DOCTYPE html><html>403 Forbidden</html>")
    tools = {t.name: t for t in build_path_tools(user_id="u1")}
    with pytest.raises(ToolSoftError) as e:
        await tools["download_url"].execute(
            {"url": "https://cdn.x.com/p.jpg", "path": "cover.jpg"})
    assert "不是图片" in str(e.value)
    assert calls["save"] == [], "拒绝时一个字节都不能落库"


@pytest.mark.asyncio
async def test_download_rejects_non_image_for_numbered_image_ref(monkeypatch):
    """[图N] 引用一定是要当配图用的，哪怕落地名没有图片后缀也要校验。"""
    calls = _stub_fs(monkeypatch, {})
    _stub_download(monkeypatch, body=b"<html>login</html>")
    tools = {t.name: t for t in build_path_tools(
        user_id="u1", image_sink=[{"url": "https://cdn.x.com/a", "source": "https://x.com/p"}])}
    with pytest.raises(ToolSoftError) as e:
        await tools["download_url"].execute({"url": "图1", "path": "pic1"})
    assert "不是图片" in str(e.value)
    assert calls["save"] == []


@pytest.mark.asyncio
async def test_download_accepts_real_image_bytes(monkeypatch):
    calls = _stub_fs(monkeypatch, {})
    _stub_download(monkeypatch, body=b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    tools = {t.name: t for t in build_path_tools(user_id="u1")}
    out = (await tools["download_url"].execute(
        {"url": "https://cdn.x.com/p.png", "path": "cover.png"})).model_content
    assert "已保存" in out
    assert [n for n, _ in calls["save"]] == ["cover.png"]


@pytest.mark.asyncio
async def test_download_does_not_sniff_non_image_targets(monkeypatch):
    """download_url 是通用搬字节工具：对归档/字体/数据集嗅探图片魔数毫无意义。"""
    calls = _stub_fs(monkeypatch, {})
    _stub_download(monkeypatch, body=b"\x1f\x8b\x08\x00random")
    tools = {t.name: t for t in build_path_tools(user_id="u1")}
    out = (await tools["download_url"].execute({"url": "https://x.com/repo.tar.gz"})).model_content
    assert "已保存" in out
    assert [n for n, _ in calls["save"]] == ["repo.tar.gz"]


# ---- DNS 解析必须有超时（2026-07-28）----
# loop.getaddrinfo 是异步的，不会冻结事件循环，但**没有超时**：坏域名 / DNS 不响应时它会
# 一路等到系统默认超时（可达数十秒），而每张图/每个下载都要解析一次——一条 download_url
# 就能把 TOOL_CALL_TIMEOUT_SECONDS=300 占满，用户看到的是"下载卡住了"。
# 同族实现 web_search_service._DNS_RESOLVE_TIMEOUT=2.0 早就有这道闸，这边一直漏着。

@pytest.mark.asyncio
async def test_slow_dns_does_not_hang_url_check(monkeypatch):
    import asyncio as _asyncio

    from app.services.chat.tools import image_fetch

    monkeypatch.setattr(image_fetch, "_DNS_RESOLVE_TIMEOUT", 0.05, raising=False)

    class _Loop:
        async def getaddrinfo(self, *_a, **_kw):
            await _asyncio.sleep(30)   # DNS 不响应
            raise AssertionError("不该等到这里")

    monkeypatch.setattr(image_fetch.asyncio, "get_running_loop", lambda: _Loop())

    started = _asyncio.get_event_loop().time()
    assert await image_fetch._host_is_public("slow.example.com") is False, (
        "解析不出来 = 判断不了是不是内网 → fail-closed 拒绝")
    assert _asyncio.get_event_loop().time() - started < 5, "必须在超时内返回，不能干等 DNS"


# ---------- 二进制文件的拒绝文案必须指路，不能说"暂未开放"（2026-07-28 P2） ----------

def test_binary_overwrite_message_points_at_bash_not_at_a_missing_capability():
    """`user_file_service` 的两处拒绝文案原先写着「需要沙箱能力（暂未开放）」——
    沙箱早就开放了（bash 就在同一批工具里）。模型照着这句话向用户报告「平台不支持修改
    该文件」，是一个纯粹由错误文案造成的能力缺失。口径必须与 paths._edit 一致。
    """
    import inspect

    from app.services.files import user_file_service

    src = inspect.getsource(user_file_service)
    assert "（暂未开放）" not in src, "沙箱早就开放了，这句话是错的"
    # 两处都要给出真正走得通的那条路
    overwrite = inspect.getsource(user_file_service.update_file_content)
    assert "bash" in overwrite and "/workspace/files/" in overwrite
    assert "python-docx" in overwrite or "openpyxl" in overwrite
    create = inspect.getsource(user_file_service.create_text_file)
    assert "bash" in create and "/workspace/files/" in create


def test_edit_tool_and_service_agree_on_how_to_change_binaries():
    """两处口径必须一致，否则模型按哪一条走全看它撞上哪个工具。"""
    import inspect

    from app.services.chat.tools import paths as paths_mod
    from app.services.files import user_file_service

    tool_text = inspect.getsource(paths_mod)
    svc_text = inspect.getsource(user_file_service.update_file_content)
    for lib in ("python-docx", "openpyxl"):
        assert lib in tool_text and lib in svc_text, f"{lib} 应在两处都被点名"


def test_build_path_tools_has_no_dead_parameter():
    """`include_unregistered` 是**死参数**（实现里从未读取），而 7 个用例在传它——
    它们自以为在测「未注册变体」，实际测的是普通路径。留着只会让下一个人继续误会。"""
    import inspect

    from app.services.chat.tools.paths import build_path_tools

    assert "include_unregistered" not in inspect.signature(build_path_tools).parameters
    # 那 7 个用例真正想验的是「edit_file 确实注册了」——直接验行为，不再靠死参数开天窗
    names = {t.name for t in build_path_tools(user_id="u1")}
    assert "edit_file" in names and "write_file" in names
