"""zip 解压体积闸门（横切支撑）：防「解压炸弹」。

背景：docx/pptx/xlsx 与 Skill 技能包本质都是 zip。单层 DEFLATE 压缩比可达约 1000:1，
十几 MB 的恶意文件解压后能吐出数 GB —— 无论是 python-docx/openpyxl/pypdf 直接解析，
还是 `archive.read(name)` 把条目整个读进内存，都会在「看清有多大」之前把 worker 内存
吃光（进程无响应 / OOM，平台级 DoS）。故所有「拿到 zip 字节就解析」的入口统一先过这里。

两道闸（缺一不可）：

1) 声明闸 :func:`ensure_zip_within_limits` / :func:`ensure_zip_bytes_within_limits`
   —— 读 zip 中央目录里的 ``ZipInfo.file_size``（解压后大小），**不解压**即可判总量 /
   单条目 / 条目数是否超限。便宜（只解析中央目录）且能挡住绝大多数炸弹。

2) 实读闸 :class:`ZipReadBudget` —— 中央目录里的声明值是攻击者可写的，实际解出来的
   字节可能远大于声明；逐条目 read 时按**真实**字节累计，超限立即抛错。

超限一律抛 :class:`ZipBombError`，``message`` 面向用户可读（调用方可原样下发给前端，
或包成 HTTP 400）；``reason`` 是同义的短原因，供附件 note / 日志用。
"""
from __future__ import annotations

import io
import zipfile
from typing import Optional

# 默认阈值（调用方按场景覆盖）：Office 文档与技能包都远小于此，正常文件不会被误伤。
DEFAULT_MAX_TOTAL_BYTES = 200 * 1024 * 1024   # 解压后总字节上限
DEFAULT_MAX_ENTRY_BYTES = 100 * 1024 * 1024   # 单条目解压后字节上限
DEFAULT_MAX_ENTRIES = 10_000                  # 条目数上限（防海量微小条目）
_READ_CHUNK_BYTES = 256 * 1024                # ZipReadBudget 分块解压的块大小


class ZipBombError(Exception):
    """zip 解压后体积/条目数超过上限（疑似解压炸弹）。

    message：面向用户的完整可读说明；reason：短原因（写 note/日志）。
    """

    def __init__(self, message: str, *, reason: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.reason = reason or message


def _mb(num: int) -> str:
    return f"{num / 1024 / 1024:.0f}MB"


def ensure_zip_within_limits(
    archive: zipfile.ZipFile,
    *,
    label: str = "文件",
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    max_entry_bytes: int = DEFAULT_MAX_ENTRY_BYTES,
    max_entries: int = DEFAULT_MAX_ENTRIES,
) -> int:
    """按中央目录声明的解压后大小校验已打开的 zip，返回声明的解压后总字节数。

    只读 ``infolist()``，不做任何解压。超限抛 :class:`ZipBombError`。
    """
    infos = archive.infolist()
    if max_entries and len(infos) > max_entries:
        raise ZipBombError(
            f"{label}内含 {len(infos)} 个文件，超过上限 {max_entries} 个，已拒绝解析",
            reason=f"压缩包条目数超限（>{max_entries}）",
        )
    total = 0
    for info in infos:
        size = int(getattr(info, "file_size", 0) or 0)
        if max_entry_bytes and size > max_entry_bytes:
            raise ZipBombError(
                f"{label}内的「{info.filename}」解压后约 {_mb(size)}，"
                f"超过单文件上限 {_mb(max_entry_bytes)}，已拒绝解析",
                reason=f"压缩包单文件解压后超限（>{_mb(max_entry_bytes)}）",
            )
        total += size
        if max_total_bytes and total > max_total_bytes:
            raise ZipBombError(
                f"{label}解压后总体积超过上限 {_mb(max_total_bytes)}（疑似解压炸弹），已拒绝解析",
                reason=f"压缩包解压后总体积超限（>{_mb(max_total_bytes)}）",
            )
    return total


def ensure_zip_bytes_within_limits(
    raw: bytes,
    *,
    label: str = "文件",
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    max_entry_bytes: int = DEFAULT_MAX_ENTRY_BYTES,
    max_entries: int = DEFAULT_MAX_ENTRIES,
) -> int:
    """字节流版本：内部开一次 zip 做声明闸校验。

    不是合法 zip（或中央目录损坏）时返回 0 **放行** —— 体积判定不是格式校验，
    格式问题交由各自的解析器（python-docx / openpyxl / …）报自己的错，别在这里改语义。
    """
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            return ensure_zip_within_limits(
                archive,
                label=label,
                max_total_bytes=max_total_bytes,
                max_entry_bytes=max_entry_bytes,
                max_entries=max_entries,
            )
    except zipfile.BadZipFile:
        return 0
    except OSError:
        return 0


class ZipReadBudget:
    """逐条目解压预算：声明值可伪造，故按**实际**读出的字节累计并提前止损。

    用法：``budget = ZipReadBudget(label="技能包"); data = budget.read(archive, name)``。
    单条目声明超限直接拒（读之前就拦住），读完后再按真实长度复核并累加总额。
    """

    def __init__(
        self,
        *,
        label: str = "文件",
        max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
        max_entry_bytes: int = DEFAULT_MAX_ENTRY_BYTES,
    ) -> None:
        self.label = label
        self.max_total_bytes = max_total_bytes
        self.max_entry_bytes = max_entry_bytes
        self.used = 0

    def _entry_too_large(self, name: str, size: Optional[int] = None) -> ZipBombError:
        approx = f"约 {_mb(size)}" if size else "过大"
        return ZipBombError(
            f"{self.label}内的「{name}」解压后{approx}，超过单文件上限 "
            f"{_mb(self.max_entry_bytes)}，已拒绝解析",
            reason=f"压缩包单文件解压后超限（>{_mb(self.max_entry_bytes)}）",
        )

    def _total_too_large(self) -> ZipBombError:
        return ZipBombError(
            f"{self.label}解压后总体积超过上限 {_mb(self.max_total_bytes)}（疑似解压炸弹），已拒绝解析",
            reason=f"压缩包解压后总体积超限（>{_mb(self.max_total_bytes)}）",
        )

    def read(self, archive: zipfile.ZipFile, name: str) -> bytes:
        """读一个条目并计入预算；超限抛 :class:`ZipBombError`。

        **分块读**（不是 ``archive.read(name)``）：中央目录声明值造假时，一次性 read 会在
        判断之前就把数 GB 解进内存 —— 这里边解边计数，越线立即停，内存封顶在上限量级。
        """
        if self.max_entry_bytes:
            try:
                declared = int(archive.getinfo(name).file_size or 0)
            except KeyError:
                declared = 0
            if declared > self.max_entry_bytes:  # 声明就超限：连读都不读
                raise self._entry_too_large(name, declared)
        remaining_total: Optional[int] = None
        if self.max_total_bytes:
            remaining_total = self.max_total_bytes - self.used
            if remaining_total <= 0:
                raise self._total_too_large()

        chunks = bytearray()
        with archive.open(name) as handle:
            while True:
                chunk = handle.read(_READ_CHUNK_BYTES)
                if not chunk:
                    break
                chunks.extend(chunk)
                if self.max_entry_bytes and len(chunks) > self.max_entry_bytes:
                    raise self._entry_too_large(name, len(chunks))
                if remaining_total is not None and len(chunks) > remaining_total:
                    self.used = self.max_total_bytes + len(chunks) - remaining_total
                    raise self._total_too_large()
        self.used += len(chunks)
        return bytes(chunks)
