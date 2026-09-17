"""统一文件系统同步层定向测试（2026-07-27）。

覆盖的都是真机踩出来的，不是想象的分支：
- `write_files` 走 put_archive/docker cp，文件落地**属主是 root**，而容器以 sandbox 运行 ——
  0644 下模型改自己的文件会 `Permission denied`（实测：bash 读得到 files/ 但 echo > 被拒）。
- 基线必须在**写入镜像之后、执行之前**打：否则「原样同步进来的文件」被当成用户本次改动，
  每轮白落一个新版本。
- 上限**不静默截断**：静默会被模型读成「文件区就这些」，比明说更糟。
- 工具描述的交付口径必须准：说错了比不说更坏——模型会按错的描述绕路，白白再跑一次命令
  交付同一个文件。（2026-07-29 起同步是唯一形态，描述不再随开关翻面。）
"""
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.services.chat.tools.shell import build_shell_tools
from app.services.chat.tools.workspace_sync import WorkspaceSync, build_sync
from app.services.sandbox import sandbox_executor
from app.services.sandbox.local_adapter import _mode_for


# ---------- 写权限（真机 Permission denied 的根因） ----------

@pytest.mark.parametrize(
    "dest,expected",
    [
        ("/workspace/files/a.txt", 0o666),          # 镜像树：模型要能改
        ("/workspace/files/sub/b.docx", 0o666),
        ("/workspace/inputs/x.xlsx", 0o644),        # 旧只读通道保持原样
        ("/workspace/skills/foo/README.md", 0o644),  # 技能包里的说明文件不需要 +x
        # 技能包里的脚本必须可执行：2026-07-27 之前是 0644，`./run.sh` 一律 rc=126
        # Permission denied，`make`（recipe 调本地脚本）同样挂 —— 而"现成 GitHub skill
        # 按它自己的 README 跑通"正是加 bash 的目的。**这条断言原来断的就是那个 bug。**
        ("/workspace/skills/foo/run.sh", 0o755),
        ("/workspace/skills/foo/scripts/build.py", 0o755),
        # 入口脚本走 `bash <path>` / `python -u <path>`，本不需要 +x；顺带拿到也无害
        ("/workspace/__main__.sh", 0o755),
        ("/workspace/__main__.py", 0o755),
    ],
)
def test_exec_scripts_get_exec_bit_and_only_files_tree_is_writable(dest, expected):
    assert _mode_for(dest) == expected


# ---------- 唯一前提：user_id ----------
#
# 2026-07-29：总开关 SANDBOX_WORKSPACE_SYNC 随旧 execute_in_sandbox 工具族一并下线，同步成为唯一
# 形态。原先这里有一条 `test_sync_off_by_default`（断言默认关闭），它连同"关闭形态"整个
# 消失；剩下的前提只有 user_id——没有它既没有文件区可镜像、也没有落库归属。

def test_sync_requires_user_id():
    assert build_sync(None) is None
    assert build_sync("") is None


def test_sync_is_on_whenever_there_is_a_user():
    """阳性对照：有 user_id 就一定建得出同步器。

    否则上面那条"没有 user_id 返回 None"可以靠 build_sync 恒返回 None 来蒙混过关。
    """
    assert build_sync("u1") is not None


@pytest.mark.asyncio
async def test_runtime_scope_exposes_only_current_thread_selected_and_revision(monkeypatch):
    rows = [
        {"id": "selected", "filename": "selected.docx", "size": 1},
        {"id": "same-thread", "filename": "draft.md", "size": 1, "threadId": "t1"},
        {"id": "revision", "filename": "target.pptx", "size": 1, "threadId": "old"},
        {"id": "hidden", "filename": "private.pdf", "size": 1, "threadId": "other"},
    ]

    async def fake_list(_uid, _folder=None):
        return {"files": rows}

    async def fake_read(_uid, fids):
        return {fid: b"x" for fid in fids}

    import app.services.files.user_file_service as ufs
    monkeypatch.setattr(ufs, "list_files", fake_list)
    monkeypatch.setattr(ufs, "read_many_bytes", fake_read)
    monkeypatch.setattr(settings, "SANDBOX_WORKSPACE_MAX_FILES", 100, raising=False)
    monkeypatch.setattr(settings, "SANDBOX_WORKSPACE_MAX_BYTES", 10 ** 9, raising=False)

    sync = WorkspaceSync(
        "u1",
        thread_id="t1",
        selected_file_ids={"selected"},
        revision_target={"file_id": "revision", "filename": "target.pptx"},
        scope_to_thread=True,
    )
    visible = await sync.prepare()
    assert set(visible) == {"selected.docx", "draft.md", "target.pptx"}
    assert "private.pdf" not in visible


@pytest.mark.asyncio
async def test_runtime_scope_restores_explicit_composer_upload_hidden_from_global_listing(monkeypatch):
    """source=workspace is hidden globally but an explicit current-turn selection is material."""
    async def fake_list(_uid, _folder=None):
        return {"files": []}

    async def fake_selected(_uid, file_ids):
        assert file_ids == ["composer-image"]
        return [{
            "id": "composer-image",
            "filename": "portrait.jpg",
            "size": 5,
            "source": "workspace",
        }]

    async def fake_read(_uid, fids):
        return {fid: b"photo" for fid in fids}

    import app.services.files.user_file_service as ufs
    monkeypatch.setattr(ufs, "list_files", fake_list)
    monkeypatch.setattr(ufs, "get_files_by_ids", fake_selected)
    monkeypatch.setattr(ufs, "read_many_bytes", fake_read)
    monkeypatch.setattr(settings, "SANDBOX_WORKSPACE_MAX_FILES", 100, raising=False)
    monkeypatch.setattr(settings, "SANDBOX_WORKSPACE_MAX_BYTES", 10 ** 9, raising=False)

    sync = WorkspaceSync(
        "u1",
        selected_file_ids={"composer-image"},
        scope_to_thread=True,
    )
    assert await sync.prepare() == {"portrait.jpg": b"photo"}


# ---------- 上限与「不静默截断」 ----------

@pytest.mark.asyncio
async def test_cap_by_file_count_reports_skipped(monkeypatch):
    monkeypatch.setattr(settings, "SANDBOX_WORKSPACE_MAX_FILES", 2, raising=False)
    monkeypatch.setattr(settings, "SANDBOX_WORKSPACE_MAX_BYTES", 10 ** 9, raising=False)

    rows = [{"id": f"id{i}", "filename": f"f{i}.txt", "size": 10} for i in range(5)]

    async def fake_list(_uid, _folder=None):
        return {"files": rows}

    async def fake_read(_uid, fids):
        return {fid: b"x" * 10 for fid in fids}

    import app.services.files.user_file_service as ufs
    monkeypatch.setattr(ufs, "list_files", fake_list)
    monkeypatch.setattr(ufs, "read_many_bytes", fake_read)

    sync = WorkspaceSync("u1")
    out = await sync.prepare()
    assert len(out) == 2
    assert len(sync.skipped) == 3
    notice = sync.notice()
    assert "只同步了 2 个" in notice
    assert "未同步" in notice
    assert "不要据此认为用户只有这些文件" in notice


@pytest.mark.asyncio
async def test_cap_by_total_bytes(monkeypatch):
    monkeypatch.setattr(settings, "SANDBOX_WORKSPACE_MAX_FILES", 100, raising=False)
    monkeypatch.setattr(settings, "SANDBOX_WORKSPACE_MAX_BYTES", 25, raising=False)

    rows = [{"id": f"id{i}", "filename": f"f{i}.txt", "size": 10} for i in range(5)]

    async def fake_list(_uid, _folder=None):
        return {"files": rows}

    async def fake_read(_uid, fids):
        return {fid: b"y" * 10 for fid in fids}

    import app.services.files.user_file_service as ufs
    monkeypatch.setattr(ufs, "list_files", fake_list)
    monkeypatch.setattr(ufs, "read_many_bytes", fake_read)

    sync = WorkspaceSync("u1")
    out = await sync.prepare()
    assert len(out) == 2, "25 字节预算只装得下两个 10 字节文件"
    assert sync.skipped


@pytest.mark.asyncio
async def test_revision_target_is_prioritized_over_smaller_history_files(monkeypatch):
    """The explicitly selected file cannot be evicted by generic small-file packing."""
    import app.services.chat.tools.workspace_sync as workspace_sync_module
    monkeypatch.setattr(
        workspace_sync_module,
        "settings",
        SimpleNamespace(SANDBOX_WORKSPACE_MAX_FILES=100, SANDBOX_WORKSPACE_MAX_BYTES=25),
    )

    rows = [
        {"id": "small-1", "filename": "a.txt", "size": 10},
        {"id": "small-2", "filename": "b.txt", "size": 10},
        {"id": "target", "filename": "selected.pptx", "size": 20},
    ]

    async def fake_list(_uid, _folder=None):
        return {"files": rows}

    async def fake_read(_uid, fids):
        sizes = {row["id"]: row["size"] for row in rows}
        return {fid: b"x" * sizes[fid] for fid in fids}

    import app.services.files.user_file_service as ufs
    monkeypatch.setattr(ufs, "list_files", fake_list)
    monkeypatch.setattr(ufs, "read_many_bytes", fake_read)

    sync = WorkspaceSync(
        "u1",
        revision_target={"file_id": "target", "filename": "selected.pptx"},
    )
    out = await sync.prepare()
    assert list(out) == ["selected.pptx"]
    assert "selected.pptx" not in sync.skipped


@pytest.mark.asyncio
async def test_no_skips_means_no_notice(monkeypatch):
    async def fake_list(_uid, _folder=None):
        return {"files": [{"id": "i1", "filename": "a.txt", "size": 3}]}

    async def fake_read(_uid, fids):
        return {fid: b"abc" for fid in fids}

    import app.services.files.user_file_service as ufs
    monkeypatch.setattr(ufs, "list_files", fake_list)
    monkeypatch.setattr(ufs, "read_many_bytes", fake_read)
    monkeypatch.setattr(settings, "SANDBOX_WORKSPACE_MAX_FILES", 100, raising=False)
    monkeypatch.setattr(settings, "SANDBOX_WORKSPACE_MAX_BYTES", 10 ** 9, raising=False)

    sync = WorkspaceSync("u1")
    await sync.prepare()
    assert sync.notice() == ""


@pytest.mark.asyncio
async def test_listing_failure_degrades_to_empty(monkeypatch):
    """文件区枚举失败不该让整个工具调用失败——降级成"没有文件"继续跑。"""
    async def boom(_uid, _folder=None):
        raise RuntimeError("db down")

    import app.services.files.user_file_service as ufs
    monkeypatch.setattr(ufs, "list_files", boom)
    assert await WorkspaceSync("u1").prepare() == {}


@pytest.mark.asyncio
async def test_missing_bytes_skips_that_file_only(monkeypatch):
    """元数据在、字节不在（历史遗留/已过期）：跳过并记下，不毁掉整批。"""
    async def fake_list(_uid, _folder=None):
        return {"files": [
            {"id": "good", "filename": "ok.txt", "size": 2},
            {"id": "bad", "filename": "gone.txt", "size": 2},
        ]}

    async def fake_read(_uid, fids):
        # 批量读的契约：读不到的**不在结果里**（read_many_bytes 静默跳过幽灵行）
        return {fid: b"ok" for fid in fids if fid != "bad"}

    import app.services.files.user_file_service as ufs
    monkeypatch.setattr(ufs, "list_files", fake_list)
    monkeypatch.setattr(ufs, "read_many_bytes", fake_read)
    monkeypatch.setattr(settings, "SANDBOX_WORKSPACE_MAX_FILES", 100, raising=False)
    monkeypatch.setattr(settings, "SANDBOX_WORKSPACE_MAX_BYTES", 10 ** 9, raising=False)

    sync = WorkspaceSync("u1")
    out = await sync.prepare()
    assert list(out) == ["ok.txt"]
    assert sync.skipped == ["gone.txt"]


# ---------- 落库：改已有 vs 新建 ----------

@pytest.mark.asyncio
async def test_persist_overwrites_known_and_creates_new(monkeypatch):
    calls: dict = {"overwrite": [], "save": []}

    async def fake_overwrite(uid, fid, data, **kw):
        calls["overwrite"].append((fid, bytes(data), kw.get("filename")))
        return {"id": fid, "filename": kw.get("filename"), "size": len(data)}

    async def fake_save(uid, name, data, **kw):
        calls["save"].append((name, bytes(data)))
        return {"id": f"new-{name}", "filename": name, "size": len(data)}

    import app.services.files.user_file_service as ufs
    monkeypatch.setattr(ufs, "overwrite_file", fake_overwrite)
    monkeypatch.setattr(ufs, "save_file", fake_save)

    sync = WorkspaceSync("u1", thread_id="t1", run_id="r1")
    sync._path_to_id["known.txt"] = "fid-1"          # 模拟同步进去过
    saved = await sync.persist([
        {"path": "known.txt", "data": b"changed"},    # 改已有 → overwrite（保 file_id）
        {"path": "fresh.txt", "data": b"new"},        # 新路径 → save
    ])
    assert calls["overwrite"] == [("fid-1", b"changed", "known.txt")]
    assert calls["save"] == [("fresh.txt", b"new")]
    # persist 回传的是**落库结果 dict**（前端文件卡靠它渲染），不是文件名
    assert sorted(r["filename"] for r in saved) == ["fresh.txt", "known.txt"]


@pytest.mark.asyncio
async def test_persist_flattens_subdir_path(monkeypatch):
    """「我的文件」是平铺+文件夹模型，没有任意深度路径 → 落库名要扁平化。"""
    names: list = []

    async def fake_save(uid, name, data, **kw):
        names.append(name)
        return {}

    import app.services.files.user_file_service as ufs
    monkeypatch.setattr(ufs, "save_file", fake_save)
    await WorkspaceSync("u1").persist([{"path": "sub/dir/a.txt", "data": b"x"}])
    assert names == ["sub_dir_a.txt"]


@pytest.mark.asyncio
async def test_persist_bundles_html_image_from_same_workspace_change(monkeypatch):
    saved: dict[str, bytes] = {}

    async def fake_save(uid, name, data, **kw):
        saved[name] = bytes(data)
        return {"id": f"new-{name}", "filename": name, "size": len(data)}

    async def no_workspace_assets(_thread_id, _user_id):
        return {}

    import app.services.files.user_file_service as ufs
    import app.services.agent_harness.workspace_service as ws
    monkeypatch.setattr(ufs, "save_file", fake_save)
    monkeypatch.setattr(ws, "asset_bytes_for_publish", no_workspace_assets)

    sync = WorkspaceSync("u1", thread_id="t1")
    result = await sync.persist([
        {"path": "index.html", "data": b'<img src="photo.jpg">'},
        {"path": "photo.jpg", "data": b"user-photo"},
    ])

    assert {row["filename"] for row in result} == {"index.html", "photo.jpg"}
    assert b"data:image/jpeg;base64," in saved["index.html"]
    assert b' src="photo.jpg"' not in saved["index.html"]
    assert sync.persist_notice() == ""


@pytest.mark.asyncio
async def test_persist_blocks_only_html_when_local_image_is_missing(monkeypatch):
    saved_names: list[str] = []

    async def fake_save(uid, name, data, **kw):
        saved_names.append(name)
        return {"id": f"new-{name}", "filename": name, "size": len(data)}

    async def no_workspace_assets(_thread_id, _user_id):
        return {}

    import app.services.files.user_file_service as ufs
    import app.services.agent_harness.workspace_service as ws
    monkeypatch.setattr(ufs, "save_file", fake_save)
    monkeypatch.setattr(ws, "asset_bytes_for_publish", no_workspace_assets)

    sync = WorkspaceSync("u1", thread_id="t1")
    result = await sync.persist([
        {"path": "broken.html", "data": b'<img src="missing.jpg">'},
        {"path": "notes.txt", "data": b"still deliver this"},
    ])

    assert [row["filename"] for row in result] == ["notes.txt"]
    assert saved_names == ["notes.txt"]
    assert sync.html_asset_failed == {"broken.html": ["missing.jpg"]}
    notice = sync.persist_notice()
    assert "HTML **没有发布**" in notice
    assert "missing.jpg" in notice


@pytest.mark.asyncio
async def test_persist_one_failure_does_not_kill_batch(monkeypatch):
    async def fake_save(uid, name, data, **kw):
        if name == "bad.txt":
            raise RuntimeError("quota")
        return {"id": f"new-{name}", "filename": name, "size": len(data)}

    import app.services.files.user_file_service as ufs
    monkeypatch.setattr(ufs, "save_file", fake_save)
    saved = await WorkspaceSync("u1").persist([
        {"path": "bad.txt", "data": b"x"},
        {"path": "good.txt", "data": b"y"},
    ])
    assert [r["filename"] for r in saved] == ["good.txt"], "单个失败不该毁整批，也不能谎报已保存"


@pytest.mark.asyncio
async def test_persist_ignores_malformed_items():
    assert await WorkspaceSync("u1").persist([{}, {"path": ""}, {"path": "a", "data": "not bytes"}]) == []


# ---------- 回写是单向的：删除/改名同步不回去 ----------

class _ListingSandbox:
    """按顺序回放 /workspace/files 清单：第一次是执行前基线，第二次是执行后。"""

    def __init__(self, listings):
        self._listings = list(listings)

    async def create(self):
        return None

    async def delete(self):
        return None

    async def write_files(self, entries):
        return None

    async def read_files(self, paths):
        return []

    async def execute(self, command, options=None):
        # 清单脚本是唯一带 os.walk 的命令（见 sandbox_executor._list_workspace_files）
        stdout = self._listings.pop(0) if "os.walk" in command else ""
        return SimpleNamespace(ok=True, stdout=stdout, stderr="", exit_code=0, truncated=False)


@pytest.mark.asyncio
async def test_deleted_files_are_reported_and_never_written_back(monkeypatch):
    """回写只比对「执行后清单」与基线，**删掉的文件根本不在执行后清单里**。

    后果不是"删除失败"这么简单：模型 rm 完看到 exit 0，转头向用户宣布"已删除"，
    而用户打开「我的文件」文件原封不动——平台替模型撒了谎。所以必须如实报上去。
    （`mv a.md b.md` 同理：产出 b.md，a.md 原封不动。）
    """
    monkeypatch.setattr(sandbox_executor.settings, "SKILL_SANDBOX_MAX_CONCURRENT", 0)
    monkeypatch.setattr(
        sandbox_executor, "create_configured_sandbox",
        lambda _name: _ListingSandbox(['[["a.md",3,1],["b.md",4,2]]', '[["b.md",4,2]]']))

    res = await sandbox_executor.execute_in_sandbox(
        "rm /workspace/files/a.md", language="bash", collect_workspace=True)

    assert res.workspace_deleted == ["a.md"]
    assert res.workspace_changes == [], "删除不产生回写——这正是要报给模型的那件事"


@pytest.mark.asyncio
async def test_unreadable_listing_is_not_mistaken_for_deletion(monkeypatch):
    """清单没问出来 ≠ 文件被删了。

    `_list_workspace_files` 解析失败时返回 None（不是 []）就是为了分开这两件事：
    合成一个值的话一次 JSON 截断就会对着用户报一串"已删除"的文件名。
    """
    monkeypatch.setattr(sandbox_executor.settings, "SKILL_SANDBOX_MAX_CONCURRENT", 0)
    monkeypatch.setattr(
        sandbox_executor, "create_configured_sandbox",
        lambda _name: _ListingSandbox(['[["a.md",3,1]]', 'Traceback (most recent call last):']))

    res = await sandbox_executor.execute_in_sandbox("ls", language="bash", collect_workspace=True)

    assert res.workspace_deleted == []
    assert res.workspace_changes == []


# ---------- 工具描述的交付口径 ----------

def test_tool_description_states_files_are_persisted():
    """描述必须说"写在 files/ 里就等于已保存"，且不能再留反口径的旧句。

    2026-07-29 这条从"随开关翻面"改成无条件：开关下线后同步是唯一形态，"不会自动存进"
    那句在任何配置下都是错的，而**错的描述比缺失的更坏**——模型会按它绕路，白白再跑一次
    命令去交付同一个文件。
    """
    desc = build_shell_tools(user_id="u1")[0].description
    assert "写在这里就等于已保存" in desc
    assert "不会自动存进" not in desc, "反口径的旧句必须彻底消失，否则模型会绕路"


# ---------- 回写数量闸（防解包污染文件区） ----------

@pytest.mark.asyncio
async def test_persist_cap_blocks_bulk_writes(monkeypatch):
    """实测：直接在 files/ 里解一个仓库归档会摊出 146 个文件变更，全落库把文件区彻底污染。

    超限必须**整批不落**并告诉模型正确做法，而不是静默丢弃、也不是硬落上百个。
    """
    calls: list = []

    async def fake_save(uid, name, data, **kw):
        calls.append(name)
        return {}

    import app.services.files.user_file_service as ufs
    monkeypatch.setattr(ufs, "save_file", fake_save)

    sync = WorkspaceSync("u1")
    # 刻意用 .md 而不是 .py：.py 会先被中间产物过滤吃掉（见 _is_intermediate），
    # 那样这条用例就测不到「超限整批不落」这道闸了
    bulk = [{"path": f"repo/f{i}.md", "data": b"x"} for i in range(sync.MAX_PERSIST + 1)]
    saved = await sync.persist(bulk)
    assert saved == []
    assert calls == [], "超限时一个都不能落"
    note = sync.persist_notice()
    assert str(sync.MAX_PERSIST + 1) in note
    assert "都没有存进" in note
    assert "/workspace/tmp" in note, "必须给出正确做法，而不是只报错"


@pytest.mark.asyncio
async def test_persist_packs_homogeneous_office_batch(monkeypatch):
    calls: list = []

    async def fake_save(uid, name, data, **kw):
        calls.append((name, data))
        return {"filename": name, "id": "f1"}

    import app.services.files.user_file_service as ufs
    monkeypatch.setattr(ufs, "save_file", fake_save)

    sync = WorkspaceSync("u1")
    bulk = [{"path": f"notice-{i:02d}.docx", "data": b"PKDOCX"} for i in range(sync.MAX_PERSIST + 5)]
    saved = await sync.persist(bulk)
    assert len(saved) == 1
    assert calls[0][0] == "batch-deliverables.zip"
    assert calls[0][1][:2] == b"PK"
    note = sync.persist_notice()
    assert "25" in note or str(sync.MAX_PERSIST + 5) in note
    assert "batch-deliverables.zip" in note
    from app.services.files.deliverable import is_deliverable, filter_rows
    assert is_deliverable("batch-deliverables.zip", "generated")
    assert filter_rows([{"filename": "batch-deliverables.zip", "source": "generated"}])


@pytest.mark.asyncio
async def test_persist_at_cap_still_saves(monkeypatch):
    """恰好等于上限时应该正常落库（边界不能少算一个）。"""
    calls: list = []

    async def fake_save(uid, name, data, **kw):
        calls.append(name)
        return {}

    import app.services.files.user_file_service as ufs
    monkeypatch.setattr(ufs, "save_file", fake_save)

    sync = WorkspaceSync("u1")
    at_cap = [{"path": f"f{i}.txt", "data": b"x"} for i in range(sync.MAX_PERSIST)]
    saved = await sync.persist(at_cap)
    assert len(saved) == sync.MAX_PERSIST
    assert sync.persist_notice() == ""


@pytest.mark.asyncio
async def test_persist_notice_empty_when_under_cap(monkeypatch):
    async def fake_save(uid, name, data, **kw):
        return {}

    import app.services.files.user_file_service as ufs
    monkeypatch.setattr(ufs, "save_file", fake_save)
    sync = WorkspaceSync("u1")
    await sync.persist([{"path": "a.txt", "data": b"x"}])
    assert sync.persist_notice() == ""


# ---------- 中间产物不当交付物（2026-07-27 真机事故） ----------

@pytest.mark.asyncio
async def test_generator_scripts_do_not_land_in_my_files(monkeypatch):
    """真机事故：用户要一份 PPT，「我的文件」里给他的是 create_ppt.py / make_water_ppt.py
    / create_water_ppt.py 三个脚本——他要的 pptx 反而混在里面找不着。

    根因是 persist() 无差别落库 files/ 下**所有**变更。MAX_PERSIST 那条闸拦不住：
    本次只有 3 个文件，离 20 很远。
    """
    saved_names: list = []

    async def fake_save(uid, name, data, **kw):
        saved_names.append(name)
        return {"id": f"new-{name}", "filename": name, "size": len(data)}

    import app.services.files.user_file_service as ufs
    monkeypatch.setattr(ufs, "save_file", fake_save)

    sync = WorkspaceSync("u1")
    saved = await sync.persist([
        {"path": "create_water_ppt.py", "data": b"import pptx"},
        {"path": "倡导多喝水.pptx", "data": b"PK\x03\x04fake"},
        {"path": "__pycache__/mod.cpython-311.pyc", "data": b"\x00"},
        {"path": "build.sh", "data": b"#!/bin/sh"},
    ])
    # 只有真交付物落库
    assert saved_names == ["倡导多喝水.pptx"]
    assert len(saved) == 1
    # 且**不静默**：明说被滤掉了、去哪写、脚本本身要交付时的出路
    note = sync.persist_notice()
    assert "create_water_ppt.py" in note
    assert "/workspace/tmp" in note
    assert "write_file" in note, "必须给出'脚本本身就是交付物'时的出路，否则是能力缺失"


def test_intermediate_filter_does_not_eat_real_deliverables():
    """白名单会把没预料到的交付格式静默吞掉，所以用的是黑名单——这条守住边界。"""
    sync = WorkspaceSync("u1")
    for good in ("report.pptx", "汇总.xlsx", "说明.md", "data.csv", "cover.svg",
                 "page.html", "结果.json", "archive.zip", "图.png", "note.txt"):
        assert sync._is_intermediate(good) is False, good
    for bad in ("gen.py", "build.sh", "a.pyc", "__pycache__/x.pyc",
                "node_modules/pkg/index.txt", ".DS_Store", "sub/.git/config"):
        assert sync._is_intermediate(bad) is True, bad


# ---------- 原位修改轮：bash 唯一能被约束的地方就是这里 ----------

def _revision_sync(**kw):
    sync = WorkspaceSync("u1", revision_target={"file_id": "fid-1", "filename": "原稿.md"}, **kw)
    sync._path_to_id["原稿.md"] = "fid-1"        # prepare() 同步进沙箱时记下的认亲表
    sync._path_to_id["别的.md"] = "fid-2"
    return sync


@pytest.mark.asyncio
async def test_revision_target_blocks_other_files_and_replacements(monkeypatch):
    """`revision_target` 非空时只准回写那一个 file_id。

    bash 不能像 create_file 那样从工具清单里摘掉（PPT 修改就是靠它跑技能脚本），命令文本也
    拦不住（`cp` / `python3 -c open(...)` 写法无穷）。而沙箱字节变成用户文件**只有这一条路**，
    闸设在这里才是物理边界。
    """
    calls: list = []

    async def fake_save(uid, name, data, **kw):
        calls.append(("save", name))
        return {"id": f"new-{name}", "filename": name, "size": len(data)}

    async def fake_overwrite(uid, fid, data, **kw):
        calls.append(("overwrite", fid))
        return {"id": fid, "filename": kw.get("filename"), "size": len(data)}

    import app.services.files.user_file_service as ufs
    monkeypatch.setattr(ufs, "save_file", fake_save)
    monkeypatch.setattr(ufs, "overwrite_file", fake_overwrite)

    sync = _revision_sync()
    saved = await sync.persist([
        {"path": "原稿.md", "data": b"revised"},      # 授权目标：放行
        {"path": "别的.md", "data": b"nope"},         # 改别的既有文件：挡
        {"path": "原稿_v2.md", "data": b"copy"},      # `cp` 另起一份：挡
    ])
    assert calls == [("overwrite", "fid-1")]
    assert [r["filename"] for r in saved] == ["原稿.md"]
    assert sync.revision_blocked == ["别的.md", "原稿_v2.md"]
    note = sync.persist_notice()
    assert "只授权原位修改" in note and "原稿_v2.md" in note, "挡下了必须说，否则模型以为已交付"


@pytest.mark.asyncio
async def test_revision_lets_the_slides_editor_companion_through(monkeypatch):
    """`<名>.slides.json` 是目标产物的可编辑源，不是"另建替代品"。

    拦掉它是静默失效的那种坏：pptx 改了，「我的文件」卡片上的「编辑」按钮还在，
    点开却是改动前的旧内容。
    """
    calls: list = []

    async def fake_save(uid, name, data, **kw):
        calls.append(name)
        return {"id": "s", "filename": name, "size": len(data)}

    async def fake_overwrite(uid, fid, data, **kw):
        calls.append(kw.get("filename"))
        return {"id": fid, "filename": kw.get("filename"), "size": len(data)}

    import app.services.files.user_file_service as ufs
    monkeypatch.setattr(ufs, "save_file", fake_save)
    monkeypatch.setattr(ufs, "overwrite_file", fake_overwrite)

    sync = WorkspaceSync("u1", revision_target={"file_id": "fid-1", "filename": "季报.pptx"})
    sync._path_to_id["季报.pptx"] = "fid-1"
    await sync.persist([
        {"path": "季报.pptx", "data": b"PK\x03\x04"},
        {"path": "季报.slides.json", "data": b"{}"},        # 伴生：放行
        {"path": "别的.slides.json", "data": b"{}"},        # 别人的伴生：照挡
    ])
    assert calls == ["季报.pptx", "季报.slides.json"]
    assert sync.revision_blocked == ["别的.slides.json"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target_name", "target_id", "peer_name"),
    [
        ("轨道经济学.pptx", "ppt-id", "轨道经济学-source.zip"),
        ("轨道经济学-source.zip", "source-id", "轨道经济学.pptx"),
    ],
)
async def test_revision_does_not_treat_legacy_source_zip_as_a_companion(
    monkeypatch, target_name, target_id, peer_name,
):
    calls: list[tuple[str, str]] = []

    async def fake_save(uid, name, data, **kw):
        calls.append(("save", name))
        return {"id": f"new-{name}", "filename": name, "size": len(data)}

    async def fake_overwrite(uid, fid, data, **kw):
        calls.append(("overwrite", kw.get("filename")))
        return {"id": fid, "filename": kw.get("filename"), "size": len(data)}

    import app.services.files.user_file_service as ufs
    monkeypatch.setattr(ufs, "save_file", fake_save)
    monkeypatch.setattr(ufs, "overwrite_file", fake_overwrite)

    sync = WorkspaceSync(
        "u1", revision_target={"file_id": target_id, "filename": target_name},
    )
    sync._path_to_id[target_name] = target_id
    await sync.persist([
        {"path": target_name, "data": b"target"},
        {"path": peer_name, "data": b"peer"},
        {"path": "其他经济学.pptx", "data": b"blocked"},
    ])
    assert [name for _, name in calls] == [target_name]
    assert sync.revision_blocked == [peer_name, "其他经济学.pptx"]


@pytest.mark.asyncio
async def test_revision_target_blocks_same_name_new_row(monkeypatch):
    """同名但不是同一行也算另建替代品——认亲必须按 file_id，不能按文件名。"""
    calls: list = []

    async def fake_save(uid, name, data, **kw):
        calls.append(name)
        return {}

    import app.services.files.user_file_service as ufs
    monkeypatch.setattr(ufs, "save_file", fake_save)

    sync = WorkspaceSync("u1", revision_target={"file_id": "fid-1", "filename": "原稿.md"})
    # 没有认亲记录（目标没能同步进沙箱）→ 落库会走 save_file 新建一行，正是"另建替代品"
    assert await sync.persist([{"path": "原稿.md", "data": b"x"}]) == []
    assert calls == []


@pytest.mark.asyncio
async def test_no_revision_target_keeps_normal_multi_file_persist(monkeypatch):
    """反向边界：普通轮次（不是原位修改）不受影响，照常多文件落库。"""
    calls: list = []

    async def fake_save(uid, name, data, **kw):
        calls.append(name)
        return {"id": "x", "filename": name, "size": 1}

    import app.services.files.user_file_service as ufs
    monkeypatch.setattr(ufs, "save_file", fake_save)

    sync = WorkspaceSync("u1")
    await sync.persist([{"path": "a.md", "data": b"1"}, {"path": "b.md", "data": b"2"}])
    assert calls == ["a.md", "b.md"]
    assert sync.persist_notice() == ""


def test_build_sync_passes_revision_target_through():
    sync = build_sync("u1", revision_target={"file_id": "fid-9", "filename": "x.pptx"})
    assert sync.revision_id == "fid-9" and sync.revision_name == "x.pptx"
    assert build_sync("u1").revision_id == "", "不传就不该有约束"


@pytest.mark.asyncio
async def test_persist_failure_is_reported_not_swallowed(monkeypatch):
    """最坏的一种沉默：命令 exit 0、产物在沙箱里、saved 为空，回执一个字不提落库失败，
    而工具描述明写「写进 files/ 就等于已保存」→ 模型向用户宣布交付完成，用户手上什么都没有。
    """
    from app.services.files.user_file_service import UserFileError

    async def boom_save(uid, name, data, **kw):
        raise UserFileError("超过单文件大小上限", status_code=413)

    import app.services.files.user_file_service as ufs
    monkeypatch.setattr(ufs, "save_file", boom_save)

    sync = WorkspaceSync("u1")
    saved = await sync.persist([{"path": "巨大.pptx", "data": b"x" * 10}])
    assert saved == []
    note = sync.persist_notice()
    assert "巨大.pptx" in note
    assert "失败" in note and "不要宣布交付完成" in note


# ---------- Run 内增量镜像（2026-07-28 P1：延迟随文件数线性增长） ----------
#
# 病灶：技能包早就有 `session.written_skills` 去重，用户文件**没有任何等价机制** ——
# 同一个 Run 内每次 bash 都从 DB+磁盘重读全部文件、再逐个 put_archive 进容器。
# 实测约 0.3 秒/文件（200 文件上限外推 60s/次），而这段时间**不在** bash 自己的超时预算里、
# 却在主循环 300s 墙钟内：光准备就能把一整轮吃掉，到点 task.cancel() 连产物都存不下来。


class _MirrorSandbox:
    """记录每次 write_files 的路径 + 可编程的 files/ 清单。"""

    instances: list = []

    def __init__(self):
        self.writes: list = []          # 每次调用写进去的路径清单
        self.listing: list = []         # [[rel, size, mtime], ...]
        # 执行用户脚本时才生效的新清单（模拟真实时序：基线在执行前拍，改动在执行中发生）
        self.after_exec: object = None
        self.created = 0
        self.removed: list = []         # 每次 `rm -f` 删掉的相对路径（反向同步断言用）
        _MirrorSandbox.instances.append(self)

    async def create(self):
        self.created += 1

    async def delete(self):
        return None

    async def write_files(self, entries):
        self.writes.append([e.path for e in entries])

    async def read_files(self, paths):
        return []

    async def execute(self, command, options=None):
        import json
        import shlex
        if command.startswith("mkdir"):
            return SimpleNamespace(ok=True, stdout="", stderr="", exit_code=0, truncated=False)
        if "os.walk" in command:  # _list_workspace_files
            return SimpleNamespace(ok=True, stdout=json.dumps(self.listing), stderr="",
                                   exit_code=0, truncated=False)
        if command.startswith("rm -f"):
            # 反向同步（_prune_stale_mirror）：真的从清单里摘掉，后面的基线/变更检测才是
            # 端到端的——只记命令不改清单的话，「删除有没有被误报成模型删的」验不出来。
            for token in shlex.split(command)[3:]:
                rel = token[len("/workspace/files/"):]
                self.removed.append(rel)
                self.listing = [row for row in self.listing if row[0] != rel]
            return SimpleNamespace(ok=True, stdout="", stderr="", exit_code=0, truncated=False)
        if "__main__.sh" in command and self.after_exec is not None:
            self.listing, self.after_exec = self.after_exec, None
        return SimpleNamespace(ok=True, stdout="", stderr="", exit_code=0, truncated=False)


@pytest.fixture
def _mirror_env(monkeypatch):
    """两个用户文件 + 复用打开 + 假沙箱。返回 (sandbox_holder, read_calls)。"""
    from app.services.sandbox import session_pool

    _MirrorSandbox.instances = []
    monkeypatch.setattr(sandbox_executor.settings, "SKILL_SANDBOX_MAX_CONCURRENT", 0)
    monkeypatch.setattr(sandbox_executor.settings, "SANDBOX_OUTPUT_REVIEW_ENABLED", False)
    monkeypatch.setattr(sandbox_executor.settings, "SANDBOX_SESSION_REUSE_ENABLED", True)
    monkeypatch.setattr(settings, "SANDBOX_WORKSPACE_MAX_FILES", 100, raising=False)
    monkeypatch.setattr(settings, "SANDBOX_WORKSPACE_MAX_BYTES", 10 ** 9, raising=False)
    monkeypatch.setattr(sandbox_executor, "create_configured_sandbox", lambda _n: _MirrorSandbox())
    monkeypatch.setattr(session_pool, "create_configured_sandbox", lambda _n: _MirrorSandbox())

    content = {"f1": b"aaa", "f2": b"bbb"}
    read_calls: list = []
    # 清单由 content 现算（2026-07-29）：`del content["f1"]` 即模拟「用户在「我的文件」里
    # 删掉了 a.txt」。此前是两行硬编码，删一个键会让 fake_list 直接 KeyError，
    # 「文件区少了一个文件」这类场景压根没法表达。
    names = {"f1": "a.txt", "f2": "b.txt"}

    async def fake_list(_uid, _folder=None):
        return {"files": [
            {"id": fid, "filename": names[fid], "size": len(content[fid])}
            for fid in ("f1", "f2") if fid in content
        ]}

    async def fake_read_many(_uid, fids):
        read_calls.append(sorted(fids))
        return {fid: content[fid] for fid in fids if fid in content}

    import app.services.files.user_file_service as ufs
    monkeypatch.setattr(ufs, "list_files", fake_list)
    monkeypatch.setattr(ufs, "read_many_bytes", fake_read_many)
    yield content, read_calls
    session_pool._sessions.clear()


def _mirrored_paths(sandbox, call_index):
    return sorted(p for p in sandbox.writes[call_index] if p.startswith("/workspace/files/"))


@pytest.mark.asyncio
async def test_second_call_in_same_run_does_not_remirror_untouched_files(_mirror_env):
    """**性能回归断言**：同一 Run 内第二次 bash 不应重新镜像任何没动过的文件。

    这条断言就是本次修复的验收线——它一旦回红，说明镜像又变回了 O(文件数)/次。
    """
    _content, read_calls = _mirror_env
    sync1 = WorkspaceSync("u1", run_id="run-A")
    sandbox = None

    async def _first():
        return await sandbox_executor.execute_in_sandbox(
            "ls", language="bash", collect_workspace=True,
            workspace_loader=sync1.load, session_key="run-A")

    # 第一次：容器是空的 → 两个文件都要写进去
    _MirrorSandbox.instances.clear()
    res1 = await _first()
    sandbox = _MirrorSandbox.instances[0]
    sandbox.listing = [["a.txt", 3, 1], ["b.txt", 3, 1]]
    assert res1.ok
    assert _mirrored_paths(sandbox, 0) == ["/workspace/files/a.txt", "/workspace/files/b.txt"]

    # 清单在执行**后**才被读到（上面赋值），所以第一次的记账要靠第二次执行来验；
    # 补一次空跑把记账回填（真实时序里执行前后各读一次清单）。
    sync_warm = WorkspaceSync("u1", run_id="run-A")
    await sandbox_executor.execute_in_sandbox("ls", language="bash", collect_workspace=True,
                               workspace_loader=sync_warm.load, session_key="run-A")

    # 第二次：容器里两个文件逐字节没变 → **一个都不该再写**
    sync2 = WorkspaceSync("u1", run_id="run-A")
    res2 = await sandbox_executor.execute_in_sandbox(
        "ls", language="bash", collect_workspace=True,
        workspace_loader=sync2.load, session_key="run-A")
    assert res2.ok
    assert _mirrored_paths(sandbox, -1) == [], "同一 Run 内没动过的文件不该重新镜像"
    assert sync2.reused_count == 2
    assert sync2.synced_count == 2, "回执要说容器里有几个文件，不能报成增量数"
    # 而 `_path_to_id` 必须照样填满 —— 少一条，persist() 就会给同一个文件反复 save_file
    # 建新记录（版本历史永远是空的，还很快吃掉文件数上限）
    assert sync2._path_to_id == {"a.txt": "f1", "b.txt": "f2"}


@pytest.mark.asyncio
async def test_changed_file_is_remirrored_next_call(_mirror_env):
    """内容变了（用户在「我的文件」里改了、或上一次执行改过）就必须重新搬。"""
    content, _read_calls = _mirror_env
    _MirrorSandbox.instances.clear()
    s1 = WorkspaceSync("u1", run_id="run-B")
    await sandbox_executor.execute_in_sandbox("ls", language="bash", collect_workspace=True,
                               workspace_loader=s1.load, session_key="run-B")
    sandbox = _MirrorSandbox.instances[0]
    sandbox.listing = [["a.txt", 3, 1], ["b.txt", 3, 1]]
    s2 = WorkspaceSync("u1", run_id="run-B")
    await sandbox_executor.execute_in_sandbox("ls", language="bash", collect_workspace=True,
                               workspace_loader=s2.load, session_key="run-B")

    content["f1"] = b"CHANGED"          # 库里那份换内容了
    s3 = WorkspaceSync("u1", run_id="run-B")
    await sandbox_executor.execute_in_sandbox("ls", language="bash", collect_workspace=True,
                               workspace_loader=s3.load, session_key="run-B")
    assert _mirrored_paths(sandbox, -1) == ["/workspace/files/a.txt"], \
        "内容变了的必须重搬，没变的不用"


@pytest.mark.asyncio
async def test_touched_file_invalidates_mirror(_mirror_env):
    """执行期间被改动过的文件，记账一律作废——沙箱里的新内容未必落得了库
    （中间产物/越权写入/超限/落库失败都会让库里仍是旧字节）。留着记账就等于让下次跳过重写。"""
    _content, _read_calls = _mirror_env
    _MirrorSandbox.instances.clear()
    s1 = WorkspaceSync("u1", run_id="run-C")
    await sandbox_executor.execute_in_sandbox("ls", language="bash", collect_workspace=True,
                               workspace_loader=s1.load, session_key="run-C")
    sandbox = _MirrorSandbox.instances[0]
    sandbox.listing = [["a.txt", 3, 1], ["b.txt", 3, 1]]
    s2 = WorkspaceSync("u1", run_id="run-C")
    await sandbox_executor.execute_in_sandbox("ls", language="bash", collect_workspace=True,
                               workspace_loader=s2.load, session_key="run-C")
    # 这一次执行把 a.txt 改了（mtime 变）→ 执行后清单与基线不同
    sandbox.after_exec = [["a.txt", 3, 999], ["b.txt", 3, 1]]
    s3 = WorkspaceSync("u1", run_id="run-C")
    await sandbox_executor.execute_in_sandbox("echo x >> files/a.txt", language="bash", collect_workspace=True,
                               workspace_loader=s3.load, session_key="run-C")

    s4 = WorkspaceSync("u1", run_id="run-C")
    await sandbox_executor.execute_in_sandbox("ls", language="bash", collect_workspace=True,
                               workspace_loader=s4.load, session_key="run-C")
    assert _mirrored_paths(sandbox, -1) == ["/workspace/files/a.txt"]


@pytest.mark.asyncio
async def test_deleted_in_sandbox_is_remirrored(_mirror_env):
    """模型在沙箱里 rm 掉的文件下次要回来 —— 用户「我的文件」里它还在，
    容器里凭空少一个而回执又说"文件区已镜像"，模型会当成用户没有这个文件。"""
    _content, _read_calls = _mirror_env
    _MirrorSandbox.instances.clear()
    s1 = WorkspaceSync("u1", run_id="run-D")
    await sandbox_executor.execute_in_sandbox("ls", language="bash", collect_workspace=True,
                               workspace_loader=s1.load, session_key="run-D")
    sandbox = _MirrorSandbox.instances[0]
    sandbox.listing = [["a.txt", 3, 1], ["b.txt", 3, 1]]
    s2 = WorkspaceSync("u1", run_id="run-D")
    await sandbox_executor.execute_in_sandbox("ls", language="bash", collect_workspace=True,
                               workspace_loader=s2.load, session_key="run-D")
    sandbox.after_exec = [["b.txt", 3, 1]]       # 执行期间 a.txt 被 rm 了
    s3 = WorkspaceSync("u1", run_id="run-D")
    res = await sandbox_executor.execute_in_sandbox("rm files/a.txt", language="bash", collect_workspace=True,
                                     workspace_loader=s3.load, session_key="run-D")
    assert res.workspace_deleted == ["a.txt"]

    s4 = WorkspaceSync("u1", run_id="run-D")
    await sandbox_executor.execute_in_sandbox("ls", language="bash", collect_workspace=True,
                               workspace_loader=s4.load, session_key="run-D")
    assert _mirrored_paths(sandbox, -1) == ["/workspace/files/a.txt"]


@pytest.mark.asyncio
async def test_new_container_gets_full_mirror(_mirror_env):
    """跨 Run（新容器）必须全量重镜像：记账挂在 session 上，会话没了记账必须跟着没。"""
    _content, _read_calls = _mirror_env
    _MirrorSandbox.instances.clear()
    for run in ("run-E", "run-F"):
        s = WorkspaceSync("u1", run_id=run)
        await sandbox_executor.execute_in_sandbox("ls", language="bash", collect_workspace=True,
                                   workspace_loader=s.load, session_key=run)
    assert len(_MirrorSandbox.instances) == 2
    for sandbox in _MirrorSandbox.instances:
        assert _mirrored_paths(sandbox, 0) == [
            "/workspace/files/a.txt", "/workspace/files/b.txt"]


@pytest.mark.asyncio
async def test_loader_failure_degrades_to_no_mirror_not_stale_skip(_mirror_env):
    """加载器炸了要降级成「本次不镜像」，**不能**按旧记账省掉写入——
    后者会让容器里缺文件却无人知晓。"""
    _content, _read_calls = _mirror_env
    _MirrorSandbox.instances.clear()

    async def boom(_mirror):
        raise RuntimeError("db down")

    res = await sandbox_executor.execute_in_sandbox("ls", language="bash", collect_workspace=True,
                                     workspace_loader=boom, session_key="run-G")
    assert res.ok
    sandbox = _MirrorSandbox.instances[0]
    assert _mirrored_paths(sandbox, 0) == []


# ---------- 反向同步：容器里的幽灵文件（2026-07-29 深扫 P1） ----------
# 病灶：`_load_workspace` 只回「要写的增量」、`write_files` 只写这些路径，**没有任何代码**
# 对比「容器里现存但新记账里已不存在」的文件并删除。`session.workspace_mirror` 只在容器判定
# 失效重建时清空，正常复用路径里旧文件一直留着。后果两级：
#   ① 用户在「我的文件」删掉的文件，模型下一次 bash 照样读到并据此作答；
#   ② 模型顺手改写它 → 回写时落库层查不到 DB 行 → 判成"新文件" → 被删的文件以新 file_id
#      **复活**进用户文件区。


async def _warm_two_files(run: str):
    """跑两次，把 a.txt/b.txt 的镜像记账坐实（第一次的清单要靠第二次才读到）。"""
    _MirrorSandbox.instances.clear()
    s1 = WorkspaceSync("u1", run_id=run)
    await sandbox_executor.execute_in_sandbox("ls", language="bash", collect_workspace=True,
                               workspace_loader=s1.load, session_key=run)
    sandbox = _MirrorSandbox.instances[0]
    sandbox.listing = [["a.txt", 3, 1], ["b.txt", 3, 1]]
    s2 = WorkspaceSync("u1", run_id=run)
    await sandbox_executor.execute_in_sandbox("ls", language="bash", collect_workspace=True,
                               workspace_loader=s2.load, session_key=run)
    assert sandbox.removed == [], "什么都没删的时候不该发 rm（也就不该多一次往返）"
    return sandbox


@pytest.mark.asyncio
async def test_file_deleted_from_my_files_is_removed_from_container(_mirror_env):
    """用户在「我的文件」删掉的文件，必须同步从容器 files/ 里删掉。

    不删的表现：模型继续读到一个已经不存在的文件；改写它还会让它以新 file_id 复活。
    """
    content, _read_calls = _mirror_env
    sandbox = await _warm_two_files("run-H")

    del content["f1"]                                  # 用户删掉了 a.txt
    s3 = WorkspaceSync("u1", run_id="run-H")
    res = await sandbox_executor.execute_in_sandbox("ls", language="bash", collect_workspace=True,
                                     workspace_loader=s3.load, session_key="run-H")
    assert res.ok
    assert sandbox.removed == ["a.txt"], "记账里没有了的镜像文件必须从容器里删掉"
    assert [row[0] for row in sandbox.listing] == ["b.txt"]
    # 没被删的那个不该被重传（反向同步不能顺手废掉增量）
    assert _mirrored_paths(sandbox, -1) == []


@pytest.mark.asyncio
async def test_pruned_file_is_not_reported_as_a_model_deletion(_mirror_env):
    """删除必须排在基线之前：否则回执会向模型报一串它根本没碰过的 workspace_deleted。

    模型看到「你删除的文件不会回写」会去解释一件自己没做过的事，甚至试图"恢复"它。
    """
    content, _read_calls = _mirror_env
    await _warm_two_files("run-I")

    del content["f1"]
    s3 = WorkspaceSync("u1", run_id="run-I")
    res = await sandbox_executor.execute_in_sandbox("ls", language="bash", collect_workspace=True,
                                     workspace_loader=s3.load, session_key="run-I")
    assert res.workspace_deleted == [], f"清理动作被误报成模型删的：{res.workspace_deleted}"


@pytest.mark.asyncio
async def test_model_created_file_is_never_pruned(_mirror_env):
    """模型自己写进 files/ 的文件**永不**参与清理，哪怕它同样不在记账里。

    这是本修复刻意留的保守边界：中间产物（workspace_sync._is_intermediate 有意不落库）、
    0 字节产物、超过单文件上限没读回的、落库失败的，全都"不在 stamps 里"——按那个判据删，
    等于在 Run 中途毁掉模型刚做出来的东西。判据只认「上次由我们镜像进去的」。
    """
    content, _read_calls = _mirror_env
    sandbox = await _warm_two_files("run-J")
    sandbox.listing = sandbox.listing + [["gen.py", 20, 7], ["草稿.pptx", 999, 8]]

    del content["f1"]
    s3 = WorkspaceSync("u1", run_id="run-J")
    await sandbox_executor.execute_in_sandbox("ls", language="bash", collect_workspace=True,
                              workspace_loader=s3.load, session_key="run-J")
    assert sandbox.removed == ["a.txt"]
    assert set(row[0] for row in sandbox.listing) == {"b.txt", "gen.py", "草稿.pptx"}


@pytest.mark.asyncio
async def test_file_touched_by_last_exec_is_not_pruned(_mirror_env):
    """上一次执行改动过的文件被剔出记账（改动未必落得了库），因此**也不参与清理**。

    容器里那份可能是模型唯一的产出，删了就没了。宁可留一个陈旧只读副本。
    这条锁的是保守边界本身：以后有人想「顺手把它也清掉」时会先撞红。
    """
    content, _read_calls = _mirror_env
    sandbox = await _warm_two_files("run-K")
    # 这一次执行把 a.txt 改了 → 记账作废（不再含 a.txt）
    sandbox.after_exec = [["a.txt", 9, 999], ["b.txt", 3, 1]]
    s3 = WorkspaceSync("u1", run_id="run-K")
    await sandbox_executor.execute_in_sandbox("echo x >> files/a.txt", language="bash", collect_workspace=True,
                              workspace_loader=s3.load, session_key="run-K")
    sandbox.removed.clear()

    del content["f1"]                                  # 随后用户把 a.txt 删了
    s4 = WorkspaceSync("u1", run_id="run-K")
    await sandbox_executor.execute_in_sandbox("ls", language="bash", collect_workspace=True,
                              workspace_loader=s4.load, session_key="run-K")
    assert sandbox.removed == [], "被模型改动过的文件不在记账里，不该被清理"
    assert "a.txt" in [row[0] for row in sandbox.listing]


@pytest.mark.asyncio
async def test_loader_failure_never_wipes_the_container(_mirror_env):
    """`stamps` 空一律不清理：loader 的失败分支和「用户真的没有文件」返回同一个空 dict。

    拿一次数据库抖动当成"用户清空了文件区"去删容器，代价远大于多留一轮幽灵文件。
    """
    _content, _read_calls = _mirror_env
    sandbox = await _warm_two_files("run-L")

    async def boom(_mirror):
        raise RuntimeError("db down")

    res = await sandbox_executor.execute_in_sandbox("ls", language="bash", collect_workspace=True,
                                     workspace_loader=boom, session_key="run-L")
    assert res.ok
    assert sandbox.removed == [], "加载器炸了却把容器里的镜像删了，这是最坏的一种放大"
    assert len(sandbox.listing) == 2


@pytest.mark.asyncio
async def test_prune_only_touches_the_files_mirror_tree(_mirror_env):
    """清理命令只能落在 /workspace/files 下，且不带 -r。

    tmp/skills/outputs/inputs 不是镜像树，里面的东西没有「用户那边已删除」这个概念；
    `-r` 会让一次路径异常有机会掀掉整棵子目录。
    """
    content, _read_calls = _mirror_env
    sandbox = await _warm_two_files("run-M")
    commands: list = []
    real_execute = sandbox.execute

    async def spy(command, options=None):
        commands.append(command)
        return await real_execute(command, options)

    sandbox.execute = spy  # type: ignore[method-assign]
    del content["f1"]
    s3 = WorkspaceSync("u1", run_id="run-M")
    await sandbox_executor.execute_in_sandbox("ls", language="bash", collect_workspace=True,
                              workspace_loader=s3.load, session_key="run-M")
    rms = [c for c in commands if c.startswith("rm ")]
    assert len(rms) == 1, f"应当只发一条 rm，实际 {rms}"
    assert rms[0] == "rm -f -- /workspace/files/a.txt"
    assert " -r" not in rms[0] and "-rf" not in rms[0]


@pytest.mark.asyncio
async def test_prune_failure_does_not_fail_the_tool_call(_mirror_env):
    """rm 失败只记日志：幽灵文件是错，但不值得让整个工具调用失败。"""
    content, _read_calls = _mirror_env
    sandbox = await _warm_two_files("run-N")
    real_execute = sandbox.execute

    async def flaky(command, options=None):
        if command.startswith("rm -f"):
            return SimpleNamespace(ok=False, stdout="", stderr="permission denied",
                                   exit_code=1, truncated=False)
        return await real_execute(command, options)

    sandbox.execute = flaky  # type: ignore[method-assign]
    del content["f1"]
    s3 = WorkspaceSync("u1", run_id="run-N")
    res = await sandbox_executor.execute_in_sandbox("ls", language="bash", collect_workspace=True,
                                     workspace_loader=s3.load, session_key="run-N")
    assert res.ok, "清理失败不该把工具调用打成失败"


@pytest.mark.asyncio
async def test_new_container_does_not_run_a_pointless_prune(_mirror_env):
    """容器探活失败降级重建时，旧容器的记账必须一起作废：新容器里什么都没有。"""
    _content, _read_calls = _mirror_env
    sandbox = await _warm_two_files("run-O")
    original_execute = sandbox.execute

    async def dead_mkdir(command, options=None):
        if command.startswith("mkdir"):
            return SimpleNamespace(ok=False, stdout="", stderr="No such container",
                                   exit_code=1, truncated=False)
        return await original_execute(command, options)

    sandbox.execute = dead_mkdir  # type: ignore[method-assign]
    s3 = WorkspaceSync("u1", run_id="run-O")
    res = await sandbox_executor.execute_in_sandbox("ls", language="bash", collect_workspace=True,
                                     workspace_loader=s3.load, session_key="run-O")
    assert res.ok
    fresh = _MirrorSandbox.instances[-1]
    assert fresh is not sandbox, "探活失败应当换了一具新容器"
    assert fresh.removed == [], "新容器里没有任何镜像，不该拿旧记账去 rm"
    # 新容器要全量重镜像（既有不变量，顺手一起锁住）
    assert _mirrored_paths(fresh, 0) == ["/workspace/files/a.txt", "/workspace/files/b.txt"]


@pytest.mark.asyncio
async def test_batched_write_is_one_round_trip():
    """写容器改成**一次** put_archive/docker cp：逐个搬的代价是结构性的（实测 0.3 秒/文件）。"""
    from app.services.sandbox.local_adapter import _tar_many
    import io
    import tarfile

    raw = _tar_many([("files/a.txt", b"A", 0o666), ("skills/s/run.sh", b"B", 0o755)])
    with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
        members = {m.name: m for m in tar.getmembers()}
    assert members["files/a.txt"].mode == 0o666
    assert members["skills/s/run.sh"].mode == 0o755
    # 中间目录要显式打进去，不依赖 docker 端的隐式 MkdirAll（各版本行为未必一致）
    assert members["files"].isdir() and members["skills/s"].isdir()


@pytest.mark.asyncio
async def test_cli_backend_batches_into_one_docker_cp_with_writable_dirs(tmp_path):
    """CLI 后端同样只走**一次** docker cp，且暂存目录每一层都放开到 0777。

    本机 agent-api 容器里没有 docker CLI（backend 自动解析成 sdk），这条路跑不到真容器，
    所以在这里把它的两条不变量钉死：
    ① 一次 cp（而不是每个文件一次——逐个 cp 的代价是结构性的，200 个文件就是一分钟）；
    ② 目录 0777。docker cp 会把宿主目录的 mode/属主应用到容器里**已存在**的同名目录，
       默认 0755 + root 属主会让 /workspace/files 变成沙箱用户不可写 —— 模型从此建不了
       新文件，整条交付链当场断掉，而这是运行时才会暴露的故障。
    """
    import os

    from app.services.sandbox.base import FileWriteEntry
    from app.services.sandbox.local_adapter import LocalDockerAdapter

    adapter = LocalDockerAdapter()
    adapter._backend = "cli"
    adapter._started = True
    calls: list = []
    seen: dict = {}

    async def fake_docker(*args, timeout=None, **_kw):
        calls.append(args)
        src = args[1].rstrip(".").rstrip("/")
        for root, dirs, files in os.walk(src):
            for d in dirs:
                p = os.path.join(root, d)
                seen[os.path.relpath(p, src)] = ("dir", os.stat(p).st_mode & 0o777)
            for f in files:
                p = os.path.join(root, f)
                seen[os.path.relpath(p, src)] = ("file", os.stat(p).st_mode & 0o777)
        return 0, b"", b""

    adapter._docker = fake_docker
    await adapter.write_files([
        FileWriteEntry(path="/workspace/__main__.sh", data=b"echo hi"),
        FileWriteEntry(path="/workspace/files/a.txt", data=b"A"),
        FileWriteEntry(path="/workspace/skills/demo/scripts/run.sh", data=b"#!/bin/bash"),
    ])

    assert len(calls) == 1, f"必须一次 cp 搬完，实际 {len(calls)} 次"
    assert calls[0][0] == "cp" and calls[0][1].endswith("/.")
    assert seen["files/a.txt"] == ("file", 0o666)
    assert seen["skills/demo/scripts/run.sh"] == ("file", 0o755)
    for d in ("files", "skills", "skills/demo", "skills/demo/scripts"):
        assert seen[d] == ("dir", 0o777), f"{d} 目录权限会传染给容器里的同名目录：{seen[d]}"


# ---------- 沙箱回写的乐观并发（2026-07-29 深扫 P1） ----------

@pytest.mark.asyncio
async def test_persist_passes_load_time_digest_as_cas(monkeypatch):
    """回写必须带装载时刻的 sha256：user_file_service.overwrite_file 的 409 闸门
    早就有了（"目标文件在任务执行期间已被更新"），而 load() 也一直在算这些摘要——
    此前只是没把原料接上，于是用户在 bash 执行期间改同一个文件会被静默盖掉。
    """
    monkeypatch.setattr(settings, "SANDBOX_WORKSPACE_MAX_FILES", 50, raising=False)
    monkeypatch.setattr(settings, "SANDBOX_WORKSPACE_MAX_BYTES", 10 ** 9, raising=False)
    from app.services.files import user_file_service as ufs

    async def fake_list(user_id, folder_id=None, **kw):
        return {"files": [{"id": "f1", "filename": "note.md", "size": 5, "source": "generated"}]}

    async def fake_read_many(user_id, ids):
        return {"f1": b"hello"}

    captured: dict = {}

    async def fake_overwrite(user_id, file_id, data, **kw):
        captured.update(kw)
        return {"id": file_id, "filename": kw.get("filename")}

    monkeypatch.setattr(ufs, "list_files", fake_list)
    monkeypatch.setattr(ufs, "read_many_bytes", fake_read_many)
    monkeypatch.setattr(ufs, "overwrite_file", fake_overwrite)

    sync = build_sync("u1", thread_id="t1", run_id="r1")
    assert sync is not None
    await sync.load()
    await sync.persist([{"path": "note.md", "data": b"hello world"}])

    import hashlib
    assert captured.get("expected_sha256") == hashlib.sha256(b"hello").hexdigest(), (
        "回写没带装载时刻摘要——CAS 闸门接不上，用户的并发编辑会被静默覆盖"
    )


# ---------- CAS 409 与"存不下"必须分开说（2026-07-29 对抗审计 P1） ----------

@pytest.mark.asyncio
async def test_cas_conflict_gets_its_own_honest_notice(monkeypatch):
    """409 = 用户在执行期间改过这个文件，处方是"重读合并"。

    原先 409 也落进 persist_failed，于是模型读到的原因是"单文件超上限/配额已满"——
    纯属编造；更糟的是它不会去重读合并，只会重试或向用户报一个假故障。
    """
    monkeypatch.setattr(settings, "SANDBOX_WORKSPACE_MAX_FILES", 50, raising=False)
    monkeypatch.setattr(settings, "SANDBOX_WORKSPACE_MAX_BYTES", 10 ** 9, raising=False)
    from app.services.files import user_file_service as ufs

    async def fake_list(user_id, folder_id=None, **kw):
        return {"files": [{"id": "f1", "filename": "note.md", "size": 5, "source": "generated"}]}

    async def fake_read_many(user_id, ids):
        return {"f1": b"hello"}

    async def conflict(user_id, file_id, data, **kw):
        raise ufs.UserFileError("目标文件在任务执行期间已被更新", status_code=409)

    monkeypatch.setattr(ufs, "list_files", fake_list)
    monkeypatch.setattr(ufs, "read_many_bytes", fake_read_many)
    monkeypatch.setattr(ufs, "overwrite_file", conflict)

    sync = build_sync("u1", thread_id="t1", run_id="r1")
    await sync.load()
    await sync.persist([{"path": "note.md", "data": b"hello world"}])

    assert sync.conflict_files == ["note.md"], "409 必须单独记账"
    assert sync.persist_failed == [], "409 不该混进「存不下」那一类"
    notice = sync.persist_notice()
    assert "用户在你执行期间自己改过" in notice
    assert "重新读取" in notice and "合并" in notice, "必须给出正确处方"
    assert "配额" not in notice.split("没有写回")[-1], "不许对 409 编造配额原因"


@pytest.mark.asyncio
async def test_non_409_failure_keeps_the_quota_wording(monkeypatch):
    """阴性对照：真正的落库失败仍走原文案（改动不能把那条说法弄丢）。"""
    monkeypatch.setattr(settings, "SANDBOX_WORKSPACE_MAX_FILES", 50, raising=False)
    monkeypatch.setattr(settings, "SANDBOX_WORKSPACE_MAX_BYTES", 10 ** 9, raising=False)
    from app.services.files import user_file_service as ufs

    async def fake_list(user_id, folder_id=None, **kw):
        return {"files": [{"id": "f1", "filename": "note.md", "size": 5, "source": "generated"}]}

    async def fake_read_many(user_id, ids):
        return {"f1": b"hello"}

    async def boom(user_id, file_id, data, **kw):
        raise ufs.UserFileError("单文件超过大小上限", status_code=400)

    monkeypatch.setattr(ufs, "list_files", fake_list)
    monkeypatch.setattr(ufs, "read_many_bytes", fake_read_many)
    monkeypatch.setattr(ufs, "overwrite_file", boom)

    sync = build_sync("u1", thread_id="t1", run_id="r1")
    await sync.load()
    await sync.persist([{"path": "note.md", "data": b"x"}])
    assert sync.persist_failed == ["note.md"] and sync.conflict_files == []
    assert "配额" in sync.persist_notice()
