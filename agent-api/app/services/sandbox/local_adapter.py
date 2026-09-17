"""Local Docker provider（MVP / 本地测试专用，ADR-047 §6.5）。

起一次性兄弟容器执行代码；生产切 opensandbox（同 SandboxAdapter 接口，改一行
SKILL_SANDBOX_PROVIDER，上层零改动）。模型：create（run -d sleep infinity 长驻）→ 传文件 →
exec 跑命令 → 取产物 → rm。

**两种后端（SKILL_SANDBOX_LOCAL_BACKEND）**：
- cli：docker CLI subprocess（host 上跑，host 有 docker CLI）。
- sdk：docker-py 走 /var/run/docker.sock（**容器内无 CLI 也可**——agent-api 容器挂 socket + pip docker）。
- auto：有 docker-py 用 sdk，否则 cli。
两后端行为契约一致（独立容器、断网、资源限额、用完即弃），host 用 cli 验证过的逻辑切 sdk 成立。

安全边界（§2.1）：墙是容器 + 镜像 + 网络，不是语言——network(默认 none 断网) + memory/cpus/pids
+ 非 root 用户 + 用完即弃。挂 socket ≈ host root，故仅本地测试、绝不进生产（生产 opensandbox K8s 隔离免 socket）。
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import io
import logging
import os
import shlex
import shutil
import tarfile
import tempfile
import time
import uuid
from typing import Optional

from app.core.config import settings

from .base import (
    ExecuteOptions,
    ExecuteResult,
    FileReadResult,
    FileWriteEntry,
    SandboxAdapter,
    SandboxError,
    SandboxInfo,
    SandboxUnavailable,
)

logger = logging.getLogger(__name__)


def _cap(b: bytes, limit: int) -> tuple[str, bool]:
    if limit and len(b) > limit:
        return b[:limit].decode("utf-8", errors="replace"), True
    return b.decode("utf-8", errors="replace"), False


async def _read_capped(stream, limit: Optional[int], on_chunk=None) -> bytes:
    """增量读取一路 stdout/stderr 流，累计超过 limit 立即停读（不等 EOF）。

    proc.communicate() 会把管道内容一次性读进内存，无节制打印的脚本能在被杀之前把宿主
    进程的 bytes 缓冲区吃到数百 MB（P1-12）；这里改成分块读并提前止损。
    on_chunk（2026-07-20 逐页产物直播）：每读到一块就回调一次；回调异常不打断读取。"""
    chunks = bytearray()
    if stream is None:
        return bytes(chunks)
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            break
        chunks.extend(chunk)
        if on_chunk is not None:
            try:
                on_chunk(bytes(chunk))
            except Exception:  # noqa: BLE001
                on_chunk = None
        if limit and len(chunks) > limit:
            break
    return bytes(chunks)


def _wrap_command(command: str, wd: str, timeout_s: int) -> str:
    """容器内 `timeout Xs sh -c 'ulimit -f N; cd wd && command'`。

    timeout 必须包整条 sh -c，否则 command 含 `&&`/`cd` 时 timeout 只作用到第一个 token
    （如 cd builtin）而失败。

    磁盘配额（2026-07-27，文档 §4.3 的候选 2）：`ulimit -f` 限**单个文件**的最大字节数。
    这是这套架构下唯一实测可用的一层——另两条都不通：
      · `--storage-opt size=` 被**静默忽略**（退出 0 但容器内 df 仍是宿主全盘）；
      · `--tmpfs /workspace` 配额真生效，但 `put_archive`/`docker cp` 绕不过挂载，
        字节落进被遮挡的下层，入口脚本直接 exit 127。
    诚实说明它的边界：**挡得住 `dd if=/dev/zero` 和解压炸弹的单个巨型输出，挡不住
    「写一万个小文件」**。真要全量配额得让 /workspace 落到带配额的卷上并改写传输方式
    （文档 §4.3 候选 3）。设 0 关闭。
    """
    limit_mb = int(getattr(settings, "SKILL_SANDBOX_LOCAL_MAX_FILE_MB", 0) or 0)
    # ulimit -f 的单位是 512 字节块（POSIX），不是 KB —— 写错单位会把上限缩小 2 倍
    prefix = f"ulimit -f {limit_mb * 1024 * 1024 // 512}; " if limit_mb > 0 else ""
    inner = f"{prefix}cd {shlex.quote(wd)} && {command}"
    return f"timeout {int(timeout_s)}s sh -c {shlex.quote(inner)}"


def _container_sleep_seconds() -> int:
    """容器 entrypoint 的存活秒数（不再是 `sleep infinity`）。

    Run 级沙箱复用后容器不再是「一次调用即拆」，agent-api 进程被 kill -9 时没人来 rm——
    让 entrypoint 自己到点退出是最后一层兜底（session_pool 的三层回收之三）。
    取会话硬生命周期上限 + 10 分钟余量，保证正常回收永远先于自毁发生。
    """
    life = int(getattr(settings, "SANDBOX_SESSION_MAX_LIFETIME_S", 3600) or 3600)
    return max(1800, life + 600)


# ---------------- 僵尸容器收割（2026-07-28） ----------------
# 病灶：entrypoint 的 `sleep` 到点后容器自己退出（见 _container_sleep_seconds），可 `docker run`
# 没带 `--rm`、SDK 侧 auto_remove 也写死 False，于是每一次自毁都留下一具 Exited(0) 空壳，
# 而且再没有任何人回来收。本机实测 31 个 agent-sbx-* **全是** Exited(0)、最早的 5 天前，
# 每个的 Created→FinishedAt 恰好等于 4200s——正是自毁那一层留下的尸体，不是别的路径。
# 空壳会一直占着容器可写层和 daemon 的容器表：长跑必然吃磁盘、拖慢每一条 docker 命令。
#
# 两条腿一起上，缺一不可：
#   1. `--rm` / auto_remove=True —— 让「自毁」真的等于「消失」，从此不再产生新空壳；
#   2. 启动 + 周期收割 —— 收掉 ① 这次改动之前就已堆着的存量，② dockerd 重启后 auto-remove
#      没兜住的，③ 被外部 `docker stop` 掉的。这三类只靠 `--rm` 永远没人管。
_CONTAINER_PREFIX = "agent-sbx-"

# 只收这两种状态：它们绝不可能再服务任何一次 exec，删了不会伤到任何人。
# **故意不收 `created`**——`docker run` 是 create+start 两步，别的进程刚 create 还没 start 的
# 容器同样是 created，从状态上无法与「启动失败的残骸」区分，误删就是把别人正在起的沙箱抽走；
# 何况我们自己的 created 残骸有 delete() 兜底（_create_attempted 一置位就负责清）。
_REAPABLE_STATES = ("exited", "dead")

_REAP_INTERVAL_S = 600.0     # 收割冷却：两次之间至少隔这么久
# 单次收割的总预算。因为收割跑在后台任务里（见 _spawn_reap），超时不占用户任何时间，
# 所以给得宽：实测机器满载时（pytest 把 16 核跑满）一次 docker socket 调用能读超时 60s，
# 卡太死等于在忙的时候永远收不掉——而忙的机器恰恰是最需要腾磁盘的那台。
_REAP_TIMEOUT_S = 120.0
_REAP_MAX_BATCH = 200        # 单次最多删这么多，剩下的下一轮再收（别把 argv 撑爆）

_last_reap_at = 0.0          # 上次收割的 monotonic 时刻；0 = 本进程还没收割过
# 后台收割任务的强引用：裸 create_task 会被 GC 静默丢弃（同 session_pool._spawn_close 的教训）
_reap_tasks: set = set()
# 本进程「已发起创建、还没 delete()」的容器名 -> 登记时刻。收割器一律跳过它们：容器即便
# 掉进 Exited（daemon 抖动 / 被外部 stop），它的 adapter 仍可能要 docker cp 取产物——
# docker cp 对**已停止**的容器照样有效，所以「已退出」并不等于「没人要了」，本进程自己
# 认领过的一律不碰。
_live_containers: dict[str, float] = {}


def _mark_live(name: str) -> None:
    _live_containers[name] = time.monotonic()


def _unmark_live(name: str) -> None:
    _live_containers.pop(name, None)


def _protected_names() -> set:
    """本进程仍在用的容器名（收割器一律跳过）。

    登记超过自毁时限的一律视为过期并摘掉：delete() 漏调时（adapter 被 GC / 异常吞掉）不能让
    一条泄漏的登记把它的容器永久钉在保护名单里——那等于给收割器开了个永不关闭的天窗。
    """
    ttl = _container_sleep_seconds()
    now = time.monotonic()
    for name, at in list(_live_containers.items()):
        if now - at >= ttl:
            _live_containers.pop(name, None)
    return set(_live_containers)


def _reap_due() -> bool:
    """到冷却点了吗？到了就顺手把冷却往前推。

    判定必须是**同步**的：`create_task` 要等下一个事件循环 tick 才跑协程体，判定若放在协程里，
    同一 tick 内并发的两个 create() 会各起一个收割任务。同步判定＋先推冷却，天然只放行一个
    （也就不需要 asyncio.Lock——模块级 Lock 会绑死第一个用到它的事件循环，pytest 换个 loop
    就报 "attached to a different loop"）。
    """
    global _last_reap_at
    interval = float(
        getattr(settings, "SANDBOX_LOCAL_REAP_INTERVAL_S", _REAP_INTERVAL_S) or _REAP_INTERVAL_S
    )
    now = time.monotonic()
    if _last_reap_at and now - _last_reap_at < interval:
        return False
    _last_reap_at = now
    return True


def _spawn_reap(adapter: "LocalDockerAdapter") -> None:
    """把收割甩到后台任务里，**绝不让它挡在 create() 前面**。

    起初是同步 await 的，实测把它否掉了：机器满载时一次 docker socket 调用会读超时 60s
    （pytest 跑满 16 核时实测到），同步 await 就等于给那次 execute_in_sandbox 白加十几秒；而收割是
    纯粹的锦上添花，一秒都不该让用户等。甩后台后超时预算反而可以给宽（见 _REAP_TIMEOUT_S）。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # 没有运行中的事件循环（同步上下文）：跳过，下次创建再说
        return
    # 先探事件循环再判冷却：反过来的话，这次白白把冷却推走，接下来 10 分钟都不会再扫。
    # 另外协程对象要等确定能起任务了再造，否则会留下 "coroutine was never awaited" 的告警。
    if not _reap_due():
        return
    task = loop.create_task(adapter._reap_zombies())
    _reap_tasks.add(task)
    task.add_done_callback(_reap_tasks.discard)


def _tar_single(name: str, data: bytes, mode: int = 0o644) -> bytes:
    """把单个文件打成 tar 字节流（docker put_archive 的输入）。

    mode 默认 0o644：沙箱非 root 用户须可读，否则 python 打不开代码文件。
    **0o666 用于 /workspace/files 这棵镜像树**——put_archive / docker cp 写进去的文件属主是
    root，而容器以 sandbox 运行，0644 下模型改自己的文件会 `Permission denied`（实测踩到：
    bash 读得到 files/ 里的文件但 `echo > file` 直接被拒）。不改成按 uid 打 tar 是因为
    沙箱镜像的 uid 未必总是 1000（远程 provider 各自不同），放宽 mode 更稳且等价——
    容器里只有 root 和 sandbox 两个身份，没有别的租户，world-writable 不扩大攻击面。
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name=name)
        info.size = len(data)
        info.mode = mode
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _tar_many(items: list) -> bytes:
    """把多个文件打成**一个** tar（items = [(相对路径, bytes, mode)]）。

    put_archive 一次一个文件时，代价是每个文件一次 daemon 往返（实测约 0.3 秒/文件）。
    一个 tar 里带上全部文件即可一次搬完；中间目录也显式打进去，不依赖 docker 端的
    隐式 MkdirAll（不同版本行为未必一致）。
    """
    buf = io.BytesIO()
    seen_dirs: set = set()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for rel, data, mode in items:
            parts = [p for p in str(rel).split("/") if p]
            if not parts:
                continue
            for i in range(1, len(parts)):
                d = "/".join(parts[:i])
                if d in seen_dirs:
                    continue
                seen_dirs.add(d)
                di = tarfile.TarInfo(name=d)
                di.type = tarfile.DIRTYPE
                di.mode = 0o777  # 与 _tar_single 的 0o666 同理：容器内非 root 要能写
                tar.addfile(di)
            info = tarfile.TarInfo(name="/".join(parts))
            info.size = len(data)
            info.mode = mode
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


# 需要可执行位的后缀。技能包里的 .sh 不给 +x，`./run.sh` 直接 rc=126 Permission denied，
# `make`（recipe 里调本地脚本）同样挂——而"现成 GitHub skill 按它自己的 README 跑通"正是
# 加 bash 的目的。2026-07-27 实测：不加这条，skills/ 下的 run.sh 一律 126。
_EXEC_EXTS = (".sh", ".bash", ".zsh", ".py", ".pl", ".rb")


def _mode_for(dest: str) -> int:
    """/workspace/files 下的镜像文件要让沙箱用户可写，见 _tar_single 的说明。"""
    if "/workspace/files/" in dest or dest.endswith("/workspace/files"):
        return 0o666
    if dest.lower().endswith(_EXEC_EXTS):
        return 0o755
    return 0o644


def _untar_first(raw: bytes) -> bytes:
    """从 docker get_archive 返回的 tar 流里取出第一个文件的字节。"""
    with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
        for member in tar.getmembers():
            if member.isfile():
                f = tar.extractfile(member)
                return f.read() if f else b""
    return b""


class LocalDockerAdapter(SandboxAdapter):
    provider = "local"

    def __init__(self, connection_config: Optional[dict] = None, create_config: Optional[dict] = None):
        cfg = connection_config or {}
        create = create_config or {}
        self._image = create.get("image") or cfg.get("image") or settings.SKILL_SANDBOX_LOCAL_IMAGE
        self._network = cfg.get("network") or settings.SKILL_SANDBOX_LOCAL_NETWORK
        self._memory = cfg.get("memory") or settings.SKILL_SANDBOX_LOCAL_MEMORY
        self._cpus = str(cfg.get("cpus") or settings.SKILL_SANDBOX_LOCAL_CPUS)
        self._pids = int(settings.SKILL_SANDBOX_LOCAL_PIDS_LIMIT)
        self._user = settings.SKILL_SANDBOX_LOCAL_USER or "sandbox"
        self._workspace = create.get("workspace") or "/workspace"
        self._container = f"agent-sbx-{uuid.uuid4().hex[:12]}"
        self._started = False
        # create() 是否已经发起过（哪怕随后被 CancelledError 打断/失败）——delete() 据此判断
        # 要不要尝试清理，而不是等 _started 置位；_started 只在 create() 完整成功后才为真，
        # 取消恰好落在 docker run -d 已提交但赋值/收尾代码没跑到的窗口时会被永远漏清（P0-7）。
        self._create_attempted = False
        # create() 被 CancelledError 打断（创建线程可能仍在后台把容器建出来）才需要
        # delete() 的重试等待兜底；普通失败时创建调用已返回，单次查找即可，不付重试延迟
        self._create_interrupted = False
        self._backend: Optional[str] = None
        self._client = None            # docker-py client（sdk 后端）
        self._sdk_container = None     # docker-py Container（sdk 后端）
        self._exec_executor: Optional[concurrent.futures.ThreadPoolExecutor] = None  # sdk exec 专属线程池（P2-10）

    # ---------- 后端选择 ----------
    def _resolve_backend(self) -> str:
        if self._backend:
            return self._backend
        pref = (settings.SKILL_SANDBOX_LOCAL_BACKEND or "auto").lower()
        if pref == "cli":
            self._backend = "cli"
        elif pref == "sdk":
            self._backend = "sdk"
        else:  # auto：有 docker-py 用 sdk（容器内首选），否则 cli（host 首选）
            try:
                import docker  # noqa: F401
                self._backend = "sdk"
            except Exception:  # noqa: BLE001
                self._backend = "cli"
        return self._backend

    def _sdk_get_client(self):
        if self._client is not None:
            return self._client
        try:
            import docker
        except Exception as exc:  # noqa: BLE001
            raise SandboxUnavailable("docker-py 未安装：pip install docker（sdk 后端需要）") from exc
        host = (settings.SKILL_SANDBOX_LOCAL_DOCKER_HOST or "").strip()
        try:
            self._client = docker.DockerClient(base_url=host) if host else docker.from_env()
        except Exception as exc:  # noqa: BLE001
            raise SandboxUnavailable(f"连接 docker socket 失败: {exc}") from exc
        return self._client

    # ---------- CLI 后端底层 ----------
    async def _docker(self, *args, timeout: Optional[float] = None, max_bytes: Optional[int] = None,
                      on_stdout=None):
        proc = await asyncio.create_subprocess_exec(
            "docker", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            # 分块读且逐路设总量上限，而非 proc.communicate() 一次性读全部（P1-12）；
            # max_bytes=None 的调用（version/cp/rm 等输出天然很小）维持原行为不设限。
            out, err = await asyncio.wait_for(
                asyncio.gather(
                    _read_capped(proc.stdout, max_bytes, on_chunk=on_stdout),
                    _read_capped(proc.stderr, max_bytes),
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            raise
        if max_bytes and (len(out) > max_bytes or len(err) > max_bytes):
            # 命中上限提前止读：进程可能仍在写，主动杀掉释放子进程/管道，不占坑等它慢慢跑完
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        if proc.returncode is None:
            await proc.wait()
        return proc.returncode, out or b"", err or b""

    # ---------- 生命周期 ----------
    async def ping(self) -> bool:
        try:
            if self._resolve_backend() == "sdk":
                return bool(await asyncio.to_thread(self._sdk_get_client().ping))
            rc, _, _ = await self._docker("version", "--format", "{{.Server.Version}}", timeout=5)
            return rc == 0
        except Exception:  # noqa: BLE001
            return False

    async def create(self) -> None:
        if self._started:
            return
        # 容器名在 __init__ 已生成；在真正发起创建前先标记"已尝试"，即便下面的 await 被
        # CancelledError 打断（docker run -d 通常比取消信号更快在 daemon 侧建好容器），
        # delete() 也据此兜底清理，不再依赖只有 create 完整成功才会置位的 _started（P0-7）。
        self._create_attempted = True
        _mark_live(self._container)  # 从这一刻起，本进程的收割器不许碰这个名字
        # 顺手收掉上一个进程留下的僵尸容器。**必须排在 _mark_live 之后**：收割协程要等下一个
        # 事件循环 tick 才跑，那时本容器已在保护名单里，绝无自己收自己的可能。甩后台不 await，
        # 理由见 _spawn_reap。
        _spawn_reap(self)
        try:
            if self._resolve_backend() == "sdk":
                await self._sdk_create()
            else:
                await self._cli_create()
        except asyncio.CancelledError:
            self._create_interrupted = True
            raise
        self._started = True

    async def ensure_running(self) -> None:
        await self.create()

    async def delete(self) -> None:
        if not self._create_attempted:
            return
        self._started = False
        try:
            if self._backend == "sdk":
                container = self._sdk_container
                if container is None:
                    # CancelledError 打断了 self._sdk_container = await ... 这行赋值，但
                    # to_thread 内的创建线程不受取消影响、大概率仍会把容器起来——按已知容器名
                    # 兜底查找。创建线程此刻可能还没跑完（还没在 daemon 侧建出可查询的容器），
                    # 短暂重试几次去赶；仍找不到就放弃，不无限空耗。
                    # 重试等待只为「取消打断、创建线程还在跑」的窗口；普通失败（镜像不存在/
                    # daemon 不可达等）创建调用已返回，单次查找即可，别让每次失败多付 1.5s
                    retries = 4 if self._create_interrupted else 1

                    async def _find_with_retry():
                        for attempt in range(retries):
                            try:
                                return await asyncio.to_thread(
                                    self._sdk_get_client().containers.get, self._container
                                )
                            except Exception:  # noqa: BLE001
                                if attempt == retries - 1:
                                    return None
                                await asyncio.sleep(0.5)
                    container = await asyncio.shield(_find_with_retry())
                if container is not None:
                    await asyncio.shield(asyncio.to_thread(container.remove, force=True))
            elif self._backend == "cli":
                await asyncio.shield(self._docker("rm", "-f", self._container, timeout=15))
        except Exception:  # noqa: BLE001
            pass
        # 无论删成没删成都摘掉保护标记：删成了自然不用护；没删成（异常被上面吞了）更要放开，
        # 好让收割器下一轮把它当僵尸收走——保护名单只保护"还在用"的，不保护"删失败"的。
        _unmark_live(self._container)
        if self._exec_executor is not None:
            self._exec_executor.shutdown(wait=False)
            self._exec_executor = None

    async def get_info(self) -> Optional[SandboxInfo]:
        return SandboxInfo(
            sandbox_id=self._container, provider=self.provider,
            state="Running" if self._started else "Created",
        )

    # ---------- CLI create ----------
    async def _cli_create(self) -> None:
        # 纵深防御（ADR-047 §7）：非 root + 断网 + 资源限额之上，再丢掉所有 Linux capability
        # 并禁止 setuid 提权。文档处理是纯用户态，不需要任何 capability。
        args = [
            "run", "-d",
            # --rm：容器一退出 daemon 就把它删掉。没有这一行，entrypoint 到点自毁只是把容器
            # 变成 Exited(0) 空壳留在 daemon 里，永远没人收（本机实测堆了 31 个，最早 5 天前）。
            # 不担心"退出后还要读产物"：读产物走 docker cp/get_archive，全发生在会话存活期内，
            # 而自毁时刻被刻意设在会话硬生命周期之后 10 分钟（见 _container_sleep_seconds），
            # 真到自毁那一刻这个沙箱早就该被回收了，没有任何合法读者。
            "--rm",
            "--name", self._container,
            "--network", self._network,
            "--memory", self._memory, "--cpus", self._cpus,
            "--pids-limit", str(self._pids),
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--user", self._user,
            "-w", self._workspace,
            # 到点自毁（见 _container_sleep_seconds）：进程被 kill 时不留长驻容器
            self._image, "sleep", str(_container_sleep_seconds()),
        ]
        try:
            rc, _, err = await self._docker(*args, timeout=30)
        except asyncio.TimeoutError as exc:
            raise SandboxUnavailable("启动本地沙箱容器超时（docker 是否可达？）") from exc
        if rc != 0:
            raise SandboxUnavailable(f"启动本地沙箱容器失败: {err.decode(errors='ignore')[:300]}")

    # ---------- SDK create ----------
    async def _sdk_create(self) -> None:
        client = self._sdk_get_client()

        def _run():
            return client.containers.run(
                # 到点自毁（见 _container_sleep_seconds）：进程被 kill 时不留长驻容器
                self._image, ["sleep", str(_container_sleep_seconds())],
                detach=True, name=self._container,
                network_mode=self._network, mem_limit=self._memory,
                nano_cpus=int(float(self._cpus) * 1_000_000_000),
                pids_limit=self._pids, user=self._user, working_dir=self._workspace,
                # 纵深防御（ADR-047 §7）：丢弃全部 capability + 禁 setuid 提权
                cap_drop=["ALL"], security_opt=["no-new-privileges"],
                # = CLI 后端的 `--rm`，理由见 _cli_create：不开这个，到点自毁只留一具
                # Exited(0) 空壳。两后端行为契约一致，这里不能是 False。
                auto_remove=True,
            )
        try:
            self._sdk_container = await asyncio.to_thread(_run)
        except Exception as exc:  # noqa: BLE001
            raise SandboxUnavailable(f"启动本地沙箱容器失败(sdk): {str(exc)[:300]}") from exc

    # ---------- 僵尸容器收割 ----------
    async def _reap_zombies(self) -> None:
        """收割一次僵尸容器；任何失败只记日志，绝不影响沙箱本身。冷却判定在 _reap_due()。

        **为什么触发点挂在 create() 上**（而不是 import 期或 lifespan 钩子）：
        - import 期起后台任务，在没有事件循环的环境（同步测试、离线脚本）会直接炸，而本模块
          是 factory 懒加载的，import 时机根本不受控；
        - lifespan 钩子在别的文件里，且 agent-api 绝大多数进程压根不碰 docker（provider 默认
          不是 local），无条件扫一遍纯属白付。create() 是唯一能确定「docker 马上要被用到」的
          时刻：此刻 daemon 必然可达，收割真失败了也只说明沙箱本来就不可用，不新增故障面。
        **为什么还留冷却、而不是只在进程首次创建时扫一次**：`--rm` 之后仍有漏网之鱼
        （dockerd 重启、被外部 stop），长跑进程得有人定期兜底；10 分钟一次 `docker ps` 的代价
        可以忽略不计。
        """
        try:
            removed = await asyncio.wait_for(self._reap_exited_containers(), timeout=_REAP_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 收割纯属锦上添花：docker 不可达/无权限/并发删同一个，一律只记日志
            logger.debug("清理僵尸沙箱容器失败（忽略，不影响沙箱可用性）: %s", exc)
            return
        if removed:
            logger.info("清理僵尸沙箱容器 %d 个: %s", len(removed), ", ".join(removed[:5]))

    async def _reap_exited_containers(self) -> list[str]:
        """删掉所有**已退出**的 agent-sbx-* 容器，返回删掉的名字（测试直接调这个）。"""
        protected = _protected_names()

        def _keep(name: str) -> bool:
            # 前缀复核不能省：docker 的 name 过滤是**子串**匹配，能捞到 "my-agent-sbx-x" 这类
            # 不是我们建的容器；名字对不上前缀的一律不碰。
            return bool(name) and name.startswith(_CONTAINER_PREFIX) and name not in protected

        if self._resolve_backend() == "sdk":
            return await self._sdk_reap(_keep)
        return await self._cli_reap(_keep)

    async def _cli_reap(self, keep) -> list[str]:
        # 同一个 key 的多个 --filter 是**或**关系（不同 key 之间才是与），所以这条 ps 的语义
        # 就是「名字含 agent-sbx- 且 状态 ∈ {exited, dead}」。运行中的容器压根不会出现在结果里。
        args = ["ps", "-a", "--filter", f"name={_CONTAINER_PREFIX}"]
        for state in _REAPABLE_STATES:
            args += ["--filter", f"status={state}"]
        args += ["--format", "{{.Names}}"]
        rc, out, err = await self._docker(*args, timeout=8)
        if rc != 0:
            raise SandboxError(f"列举僵尸沙箱容器失败: {err.decode(errors='ignore')[:200]}")
        victims = [n for n in (ln.strip() for ln in out.decode(errors="ignore").splitlines()) if keep(n)]
        if not victims:
            return []
        victims = victims[:_REAP_MAX_BATCH]
        # **不带 -f**：万一哪个 docker 版本把 status 过滤器理解成别的意思、回了个还在跑的容器，
        # docker 自己会拒绝删除（"container is running"）。这是防误删的最后一道硬闸，
        # 加了 -f 就等于亲手拆掉它——收僵尸永远不值得用 force。
        rc, _, err = await self._docker("rm", *victims, timeout=12)
        if rc != 0:
            # 批量删部分失败（多半是并发收割撞车 / 刚被别人删走）：其余照删不误，只记日志
            logger.debug("批量删除僵尸容器有失败项（忽略）: %s", err.decode(errors="ignore")[:200])
        return victims

    async def _sdk_reap(self, keep) -> list[str]:
        client = self._sdk_get_client()

        def _list():
            return client.containers.list(
                all=True,  # 不带 all 只回运行中的，正好把要收的全滤没了
                filters={"name": _CONTAINER_PREFIX, "status": list(_REAPABLE_STATES)},
            )

        containers = await asyncio.to_thread(_list)
        removed: list[str] = []
        for container in list(containers)[:_REAP_MAX_BATCH]:
            name = str(getattr(container, "name", "") or "")
            # 服务端过滤器已经筛过一遍，客户端再按状态复核一次：过滤器语义万一随 docker /
            # docker-py 版本漂移，这道复核就是「绝不误删运行中容器」的兜底。
            status = str(getattr(container, "status", "") or "").lower()
            if not keep(name) or status not in _REAPABLE_STATES:
                continue
            try:
                # 同 CLI：**不传 force**，还在跑的容器交给 docker 自己拒绝
                await asyncio.to_thread(container.remove)
            except Exception as exc:  # noqa: BLE001 并发删同一个 / 已被别人收走：跳过继续，别让一个失败带走整轮
                logger.debug("删除僵尸容器 %s 失败（忽略）: %s", name, exc)
                continue
            removed.append(name)
        return removed

    # ---------- 执行 ----------
    async def execute(self, command: str, options: Optional[ExecuteOptions] = None) -> ExecuteResult:
        if not self._started:
            await self.create()
        timeout_s = (
            (options.timeout_ms // 1000) if (options and options.timeout_ms)
            else settings.SKILL_SANDBOX_LOCAL_TIMEOUT_MS // 1000
        )
        wd = (options.working_directory if options else None) or self._workspace
        wrapped = _wrap_command(command, wd, timeout_s)
        max_bytes = (
            (options.max_output_bytes if options and options.max_output_bytes else None)
            or settings.SKILL_SANDBOX_LOCAL_MAX_OUTPUT_BYTES
        )
        on_stdout = options.on_stdout if options else None
        try:
            if self._backend == "sdk":
                rc, out, err = await self._sdk_exec(
                    wrapped, timeout_s + 10, on_stdout=on_stdout, max_bytes=max_bytes,
                )
            else:
                rc, out, err = await self._docker(
                    "exec", self._container, "sh", "-lc", wrapped, timeout=timeout_s + 10,
                    max_bytes=max_bytes, on_stdout=on_stdout,
                )
        except asyncio.TimeoutError:
            return ExecuteResult(
                stdout="", stderr=f"执行超时（{timeout_s}s）", exit_code=124,
                truncated=True, timed_out=True, termination_reason="timeout",
            )
        so, t1 = _cap(out, max_bytes)
        se, t2 = _cap(err, max_bytes)
        exit_code = rc if rc is not None else 1
        return ExecuteResult(
            stdout=so, stderr=se, exit_code=exit_code, truncated=t1 or t2,
            timed_out=exit_code == 124,
            termination_reason="timeout" if exit_code == 124 else None,
        )

    async def set_env_prep_network(self, enabled: bool) -> bool:
        # local 容器网络在 create 时已钉死。已经出网的镜像可以装依赖；默认 none 不能中途改网。
        return str(self._network or "none") not in {"", "none"}

    def _get_exec_executor(self) -> concurrent.futures.ThreadPoolExecutor:
        if self._exec_executor is None:
            self._exec_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        return self._exec_executor

    async def _sdk_exec(self, wrapped: str, wait_s: float, on_stdout=None, max_bytes: Optional[int] = None):
        """sdk 后端执行；边读边计数，任一路累计超过 max_bytes 立即停读（对齐 CLI 后端 _read_capped）。

        统一走低层 exec API 流式读——`exec_run()`（无论是否 demux）会把输出**读完**才返回，
        `while True: print(...)` 这类无节制打印能在 60s 超时前把宿主进程内存攒到数百 MB
        （P1-13 在 opensandbox provider 修过的同类问题）。exec_run(stream=True) 又拿不到
        exit code，故 exec_create/exec_start(stream, demux) 逐块读 + exec_inspect 取码，
        流式回调（2026-07-20 逐页产物直播）也复用这条路径。
        """
        def _wait_exit_code(api, ex_id, attempts: int = 5, delay_s: float = 0.1):
            """exec_inspect 的 ExitCode 在流刚关闭时可能瞬时为 None（daemon 未收割）——
            小步轮询拿真实退出码，别把「输出多但已跑完」的成功脚本误判成失败
            （深扫收尾 2026-07-26：ok=False 会让模型返工重跑写文件类脚本＝重复副作用）。"""
            import time as _time

            for i in range(attempts):
                info = api.exec_inspect(ex_id) or {}
                rc = info.get("ExitCode")
                if rc is not None and not info.get("Running"):
                    return rc
                if i < attempts - 1:
                    _time.sleep(delay_s)
            return None

        def _exec():
            api = self._sdk_get_client().api
            ex_id = api.exec_create(self._sdk_container.id, ["sh", "-lc", wrapped])["Id"]
            out_buf, err_buf = bytearray(), bytearray()
            capped = False
            callback = on_stdout
            stream = api.exec_start(ex_id, stream=True, demux=True)
            try:
                for so, se in stream:
                    if so:
                        out_buf.extend(so)
                        if callback is not None:
                            try:
                                callback(bytes(so))
                            except Exception:  # noqa: BLE001
                                callback = None
                    if se:
                        err_buf.extend(se)
                    # 严格大于：等于上限时不算截断，与 _cap()/_read_capped 口径一致
                    if max_bytes and (len(out_buf) > max_bytes or len(err_buf) > max_bytes):
                        capped = True
                        break
            finally:
                # 提前止读才需要主动断流（daemon 侧连接留着的话容器内进程继续写、我们继续被
                # 喂数据，CLI 后端在同一位置 proc.kill()）；正常读尽的流不折腾底层 socket。
                if capped:
                    close = getattr(stream, "close", None)
                    if close is not None:
                        try:
                            close()
                        except Exception:  # noqa: BLE001
                            pass
            # 截断与否都尽力取真实退出码：截断的常见情形是「输出超限但脚本已正常跑完」。
            # 仍在跑的失控进程拿不到码（返回 None → execute() 归一为 1），由 _wrap_command
            # 的 `timeout Xs` 兜底自杀，与 CLI 后端 proc.kill() 同一口径。
            rc = _wait_exit_code(api, ex_id)
            return rc, bytes(out_buf), bytes(err_buf)
        # docker exec API 无超时参数；容器内 timeout 命令兜底，这里再包 wait_for 防容器整体僵死。
        # docker-py 的 exec 调用是同步阻塞 HTTP，wait_for 超时只让等待方放弃，无法真正
        # 取消已提交的线程——用专属单线程池（而非共享的 asyncio 默认 executor）提交，超时后
        # 立即丢弃该池（shutdown(wait=False)，不等滞留线程收尾）并在下次调用重建，避免一次
        # daemon 卡顿占住的 worker 拖累全应用其它 to_thread/run_in_executor 调用（P2-10）。
        executor = self._get_exec_executor()
        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(loop.run_in_executor(executor, _exec), timeout=wait_s)
        except asyncio.TimeoutError:
            executor.shutdown(wait=False)
            self._exec_executor = None
            raise

    # ---------- 写文件 ----------
    async def write_files(self, entries: list[FileWriteEntry]) -> None:
        if not self._started:
            await self.create()
        if self._backend == "sdk":
            await self._sdk_write(entries)
        else:
            await self._cli_write(entries)

    def _split_under_workspace(self, entries: list[FileWriteEntry]) -> tuple[list, list]:
        """按「是否落在 /workspace 树下」分成 (可批量, 需逐个)。

        批量的前提是能算出一个共同的解包根。绝大多数写入（脚本 + files/ 镜像 + skills/ 包）
        都在 /workspace 下，所以这一刀就够；不在的老老实实逐个走，行为与批量前完全一致。
        """
        root = self._workspace.rstrip("/") + "/"
        batch, rest = [], []
        for entry in entries:
            dest = entry.path if entry.path.startswith("/") else f"{self._workspace}/{entry.path}"
            (batch if dest.startswith(root) else rest).append((dest, entry))
        return batch, rest

    async def _cli_write(self, entries: list[FileWriteEntry]) -> None:
        """一次 `docker cp` 搬完（而不是每个文件一次）。

        逐个 cp 的代价是**结构性**的：每个文件一次 docker CLI 进程 + 一次 daemon 往返，
        实测约 0.3 秒/文件——文件区上限 200 个就是一分钟，而这段时间不在工具自己的超时
        预算里。docker cp 支持 `SRC_DIR/. → CONTAINER:DEST` 语义（拷目录**内容**），
        所以在宿主临时目录里按相对路径摆好再一次拷进去即可。
        """
        batch, rest = self._split_under_workspace(entries)
        if batch:
            staging = tempfile.mkdtemp(prefix="agent-sbx-write-")
            try:
                root_len = len(self._workspace.rstrip("/")) + 1
                made_dirs: set = set()
                for dest, entry in batch:
                    rel = dest[root_len:]
                    local = os.path.join(staging, *rel.split("/"))
                    parent = os.path.dirname(local)
                    os.makedirs(parent, exist_ok=True)
                    # ⚠️ 目录 mode 必须放开到 0777，而且**每一层**都要。docker cp 会把宿主
                    # 目录的 mode/属主一并应用到容器里**已存在**的同名目录 —— 默认 0755 +
                    # root 属主会让 /workspace/files 变成沙箱用户不可写，模型从此再也建不了
                    # 新文件，整条交付链当场断掉而单测一个字都看不出来。理由同 _tar_single
                    # 的 0o666：容器里只有 root 和 sandbox 两个身份，没有别的租户。
                    node = parent
                    while node != staging and node not in made_dirs and node.startswith(staging):
                        made_dirs.add(node)
                        os.chmod(node, 0o777)
                        node = os.path.dirname(node)
                    with open(local, "wb") as fh:
                        fh.write(entry.data)
                    # docker cp 保留宿主 mode，0600 会让容器内非 root 用户读不了
                    os.chmod(local, _mode_for(dest))
                rc, _, err = await self._docker(
                    "cp", f"{staging}/.", f"{self._container}:{self._workspace}", timeout=120)
                if rc != 0:
                    raise SandboxError(
                        f"写文件失败（{len(batch)} 个）: {err.decode(errors='ignore')[:200]}")
            finally:
                shutil.rmtree(staging, ignore_errors=True)
        for dest, entry in rest:
            with tempfile.NamedTemporaryFile(delete=False) as tf:
                tf.write(entry.data)
                tmp = tf.name
            os.chmod(tmp, _mode_for(dest))
            try:
                rc, _, err = await self._docker("cp", tmp, f"{self._container}:{dest}", timeout=30)
                if rc != 0:
                    raise SandboxError(f"写文件失败 {dest}: {err.decode(errors='ignore')[:200]}")
            finally:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    async def _sdk_write(self, entries: list[FileWriteEntry]) -> None:
        """一次 `put_archive` 搬完（理由同 _cli_write）。"""
        batch, rest = self._split_under_workspace(entries)
        if batch:
            root_len = len(self._workspace.rstrip("/")) + 1
            tar_bytes = _tar_many([(dest[root_len:], e.data, _mode_for(dest)) for dest, e in batch])
            try:
                ok = await asyncio.to_thread(
                    self._sdk_container.put_archive, self._workspace, tar_bytes)
                if not ok:
                    raise SandboxError(f"写文件失败（{len(batch)} 个）")
            except SandboxError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise SandboxError(f"写文件失败（{len(batch)} 个）: {str(exc)[:200]}") from exc
        for dest, entry in rest:
            directory, _, name = dest.rpartition("/")
            directory = directory or "/"
            tar_bytes = _tar_single(name, entry.data, _mode_for(dest))
            try:
                ok = await asyncio.to_thread(self._sdk_container.put_archive, directory, tar_bytes)
                if not ok:
                    raise SandboxError(f"写文件失败 {dest}")
            except SandboxError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise SandboxError(f"写文件失败 {dest}: {str(exc)[:200]}") from exc

    # ---------- 读文件 ----------
    async def read_files(self, paths: list[str]) -> list[FileReadResult]:
        if not self._started:
            await self.create()
        if self._backend == "sdk":
            return await self._sdk_read(paths)
        return await self._cli_read(paths)

    async def _cli_read(self, paths: list[str]) -> list[FileReadResult]:
        results: list[FileReadResult] = []
        for path in paths:
            src = path if path.startswith("/") else f"{self._workspace}/{path}"
            with tempfile.NamedTemporaryFile(delete=False) as tf:
                tmp = tf.name
            try:
                rc, _, err = await self._docker("cp", f"{self._container}:{src}", tmp, timeout=30)
                if rc != 0:
                    results.append(FileReadResult(path=path, error=err.decode(errors="ignore")[:200]))
                    continue
                with open(tmp, "rb") as fh:
                    results.append(FileReadResult(path=path, data=fh.read()))
            except Exception as exc:  # noqa: BLE001
                results.append(FileReadResult(path=path, error=str(exc)[:200]))
            finally:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        return results

    async def _sdk_read(self, paths: list[str]) -> list[FileReadResult]:
        results: list[FileReadResult] = []
        for path in paths:
            src = path if path.startswith("/") else f"{self._workspace}/{path}"

            def _get():
                bits, _ = self._sdk_container.get_archive(src)
                return b"".join(bits)
            try:
                raw = await asyncio.to_thread(_get)
                results.append(FileReadResult(path=path, data=_untar_first(raw)))
            except Exception as exc:  # noqa: BLE001
                results.append(FileReadResult(path=path, error=str(exc)[:200]))
        return results
