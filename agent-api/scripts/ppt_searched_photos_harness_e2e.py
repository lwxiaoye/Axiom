#!/usr/bin/env python3
"""Live Harness E2E for searched-photo PPT delivery.

Run inside the API container:

    python -u scripts/ppt_searched_photos_harness_e2e.py

The check uses the production Worker and gpt-5.5. It requires evidence from the
event stream and from the generated PPTX bytes; model claims are not accepted as
proof. The temporary thread and newly generated user files are always removed.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import sys
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from PIL import Image

sys.path.insert(0, "/app")

UID = "2085197534705397761"
USERNAME = "wushaoran"
MODEL = "gpt-5.5"
TERMINAL_EVENTS = {
    "run.completed", "run.partial", "run.failed", "run.cancelled",
}


def _media_facts(pptx: bytes) -> tuple[int, list[dict[str, Any]]]:
    facts: list[dict[str, Any]] = []
    with zipfile.ZipFile(io.BytesIO(pptx)) as archive:
        bad = archive.testzip()
        if bad:
            raise AssertionError(f"PPTX ZIP is corrupt at {bad}")
        slide_count = len([
            name for name in archive.namelist()
            if name.startswith("ppt/slides/slide") and name.endswith(".xml")
        ])
        for info in archive.infolist():
            if info.is_dir() or not info.filename.startswith("ppt/media/"):
                continue
            payload = archive.read(info.filename)
            try:
                with Image.open(io.BytesIO(payload)) as image:
                    width, height = image.size
                    image_format = str(image.format or "")
            except Exception:  # noqa: BLE001
                continue
            facts.append({
                "name": Path(info.filename).name,
                "bytes": len(payload),
                "width": width,
                "height": height,
                "format": image_format,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "payload": payload,
            })
    return slide_count, facts


def _event_name(event: dict[str, Any]) -> str:
    return str((event.get("data") or {}).get("name") or "")


def _message(filename: str) -> str:
    return (
        "请直接制作并一次性交付一份 4 页中文 PPTX，主题是《库里：改变比赛的射手》。"
        "采用勇士蓝金配色，必须先联网搜索并使用至少 3 张不同来源的库里 NBA 真实比赛现场照片，"
        "照片要作为页面主要视觉，禁止插画、火柴人、海报、界面截图或占位图。"
        "请在本轮内完成搜图、制作、渲染审查和修正，"
        f"最后只交付一个文件名为 {filename} 的 .pptx，不要 ZIP，不要停下来询问我。"
    )


def _source_domain(receipt: dict[str, Any]) -> str:
    for field in ("source_page", "source_url"):
        try:
            host = urlparse(str(receipt.get(field) or "")).hostname or ""
        except ValueError:
            continue
        host = host.rstrip(".").lower()
        if host:
            return host[4:] if host.startswith("www.") else host
    return ""


async def _preflight_sandbox_runtime() -> str:
    """Prove the configured sandbox image can execute the PPTD exporter."""
    from app.services.sandbox import ExecuteOptions, create_configured_sandbox

    sandbox = create_configured_sandbox("ppt-runtime-preflight")
    if not await sandbox.ping():
        raise RuntimeError("configured sandbox provider is not healthy")
    await sandbox.create()
    try:
        result = await sandbox.execute(
            "set -eu\n"
            "node_major=\"$(node -p 'process.versions.node.split(\".\")[0]')\"\n"
            "test \"$node_major\" -ge 18\n"
            "python -c \"import yaml\"\n"
            "test -s /opt/open-kimi-ppt/scripts/export_pptx.py\n"
            "test -s /opt/open-kimi-ppt/scripts/local-export/export-pptd.mjs\n"
            "test -s /opt/open-kimi-ppt/scripts/local-export/pptd_wasm_bg.wasm\n"
            "node --check /opt/open-kimi-ppt/scripts/local-export/export-pptd.mjs\n"
            "python -c \"from pathlib import Path; "
            "assert Path('/opt/open-kimi-ppt/scripts/local-export/pptd_wasm_bg.wasm')"
            ".read_bytes()[:4] == b'\\\\x00asm'\"\n"
            "printf 'node=%s ppt-runtime=ready\\n' \"$(node --version)\"\n",
            ExecuteOptions(timeout_ms=30_000, max_output_bytes=16_384),
        )
        if not result.ok:
            detail = (result.stderr or result.stdout or "unknown error").strip()
            raise RuntimeError(
                f"configured sandbox image lacks the PPT export runtime: {detail}"
            )
        return result.stdout.strip()
    finally:
        await sandbox.delete()


async def _collect(run_id: str) -> list[dict[str, Any]]:
    from app.services import sse_protocol
    from app.services.agent_harness.orchestrator import harness_orchestrator
    from app.services.tasks import task_run_service

    events: list[dict[str, Any]] = []
    async with asyncio.timeout(3600):
        async for payload in harness_orchestrator.subscribe_run(
            user_id=UID,
            run_id=run_id,
            protocol=sse_protocol.HARNESS,
        ):
            event = task_run_service.parse_harness_sse_payload(payload)
            if not event:
                continue
            events.append(event)
            event_type = str(event.get("type") or "")
            data = event.get("data") or {}
            if event_type == "tool.started":
                print(
                    f"  tool.started {data.get('name')}: "
                    f"{json.dumps(data.get('args') or {}, ensure_ascii=False)[:220]}",
                    flush=True,
                )
            elif event_type in {"tool.failed", "run.failed", "run.partial"}:
                print(
                    f"  {event_type}: {json.dumps(data, ensure_ascii=False)[:600]}",
                    flush=True,
                )
            if event_type in TERMINAL_EVENTS:
                break
    return events


async def main() -> int:
    from app.core.auth import UserContext
    from app.services import sse_protocol
    from app.services.agent_harness.orchestrator import harness_orchestrator
    from app.services.files import user_file_service
    from app.services.chat.turn_context_builder import (
        _fetch_trusted_skills,
        _get_catalog_records,
    )
    from app.services.skills.ppt_policy import effective_ppt_skill_ids
    from app.services.skills.skill_package_bridge import fetch_skill_packages

    access_token = str(os.environ.get("E2E_ACCESS_TOKEN") or "").strip()
    if not access_token:
        print("FAIL: set E2E_ACCESS_TOKEN from a real browser login", flush=True)
        return 2

    try:
        runtime_summary = await _preflight_sandbox_runtime()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: PPT sandbox runtime preflight failed: {exc}", flush=True)
        return 2
    print(f"[0] sandbox runtime preflight passed: {runtime_summary}", flush=True)

    deliverable_name = f"库里-真实比赛照片-QA-{int(time.time() * 1000)}.pptx"
    message = _message(deliverable_name)
    catalog = await _get_catalog_records(access_token)
    skill_ids = effective_ppt_skill_ids(message, [], catalog)
    trusted_skills = await _fetch_trusted_skills(skill_ids, access_token)
    ppt_skills = [skill for skill in trusted_skills if skill.get("is_ppt_skill")]
    packages = await fetch_skill_packages(ppt_skills, access_token)
    mounted_ppt_packages = [
        package for package in packages
        if not package.get("unavailable")
        and "SKILL.md" in (package.get("files") or {})
        and "scripts/run_export.py" in (package.get("files") or {})
    ]
    if not skill_ids or not ppt_skills or not mounted_ppt_packages:
        print(
            "FAIL: authenticated ppt-studio package preflight did not produce a mountable package",
            flush=True,
        )
        return 2
    print(
        f"[1] ppt-studio preflight passed: skill_ids={len(skill_ids)} "
        f"packages={len(mounted_ppt_packages)}",
        flush=True,
    )

    before = await user_file_service.list_files(UID, "__all__")
    before_ids = {
        str(row.get("id") or "") for row in (before.get("files") or []) if row.get("id")
    }
    thread_id = ""
    run_id = ""
    new_file_ids: list[str] = []
    try:
        newapi_key, resolved_model = await harness_orchestrator.prepare_chat(UID, MODEL)
        assert resolved_model == MODEL, f"model drifted to {resolved_model}"
        user = UserContext(
            user_id=UID,
            username=USERNAME,
            tenant_id="0",
            access_token=access_token,
        )
        print(f"[2] accepting live run with {resolved_model}", flush=True)
        started = await harness_orchestrator.accept_harness_run(
            user_id=UID,
            message=message,
            thread_id=None,
            user_context=user,
            model=resolved_model,
            resolved_model=resolved_model,
            newapi_key=newapi_key,
            token=access_token,
            protocol=sse_protocol.HARNESS,
            agent_mode="standard",
            web_search=True,
            skill_ids=skill_ids,
            selected_skills=[
                {"id": skill["id"], "name": skill["name"]}
                for skill in ppt_skills
            ],
            knowledge_ids=[],
            selected_knowledge=[],
            attachments=[],
            file_ids=[],
            thread_ids=[],
            client_request_id=f"ppt-photo-harness-e2e-{int(time.time() * 1000)}",
        )
        thread_id = str(started["thread_id"])
        run_id = str(started["run_id"])
        print(f"    thread={thread_id} run={run_id}", flush=True)

        events = await _collect(run_id)
        terminal = next(
            (str(event.get("type")) for event in reversed(events)
             if str(event.get("type")) in TERMINAL_EVENTS),
            "missing",
        )
        started_tools = [
            _event_name(event) for event in events if event.get("type") == "tool.started"
        ]
        completed_tools = [
            _event_name(event) for event in events if event.get("type") == "tool.completed"
        ]
        failed_tools = [
            _event_name(event) for event in events if event.get("type") == "tool.failed"
        ]
        asset_receipts = [
            receipt
            for event in events
            if event.get("type") == "tool.completed" and _event_name(event) == "fetch_ppt_asset"
            for receipt in [((event.get("data") or {}).get("meta") or {}).get("asset_receipt")]
            if isinstance(receipt, dict)
        ]
        source_domains = {
            domain
            for receipt in asset_receipts
            for domain in [_source_domain(receipt)]
            if domain
        }
        receipts = [
            row
            for event in events if event.get("type") == "artifact.saved"
            for row in ((event.get("data") or {}).get("files") or [])
            if isinstance(row, dict)
        ]
        receipt_ids = {
            str(row.get("id") or row.get("file_id") or "") for row in receipts
            if row.get("id") or row.get("file_id")
        }

        after = await user_file_service.list_files(UID, "__all__")
        new_rows = [
            row for row in (after.get("files") or [])
            if str(row.get("id") or "") not in before_ids
        ]
        new_file_ids = [str(row.get("id")) for row in new_rows if row.get("id")]
        pptx_rows = [
            row for row in new_rows
            if str(row.get("filename") or "").lower().endswith(".pptx")
        ]
        zip_rows = [
            row for row in new_rows
            if str(row.get("filename") or "").lower().endswith(".zip")
        ]

        print(
            f"[3] terminal={terminal} started_tools={started_tools} "
            f"failed_tools={failed_tools}",
            flush=True,
        )
        print(
            f"[4] new_files={[row.get('filename') for row in new_rows]} "
            f"receipts={[row.get('filename') for row in receipts]}",
            flush=True,
        )

        slide_count = 0
        media: list[dict[str, Any]] = []
        output_dir = Path(f"/tmp/ppt-searched-photos-{run_id}")
        if len(pptx_rows) == 1:
            pptx_row = pptx_rows[0]
            _row, pptx_bytes = await user_file_service.read_bytes(UID, str(pptx_row["id"]))
            slide_count, media = _media_facts(pptx_bytes)
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / str(pptx_row["filename"])).write_bytes(pptx_bytes)
            media_dir = output_dir / "media"
            media_dir.mkdir(exist_ok=True)
            for item in media:
                (media_dir / str(item["name"])).write_bytes(item["payload"])
            print(f"[5] retained QA copy at {output_dir}", flush=True)

        substantive = [
            item for item in media
            if int(item["width"]) * int(item["height"]) >= 300_000
            and int(item["bytes"]) >= 30_000
        ]
        unique_substantive = {str(item["sha256"]) for item in substantive}
        print(
            "[6] pptx facts: "
            f"slides={slide_count} media={len(media)} "
            f"substantive={len(substantive)} unique={len(unique_substantive)}",
            flush=True,
        )
        for item in substantive:
            print(
                f"    {item['name']} {item['format']} {item['width']}x{item['height']} "
                f"{item['bytes']} bytes sha256={str(item['sha256'])[:16]}",
                flush=True,
            )

        checks = {
            "run_completed": terminal == "run.completed",
            "used_search_web": "search_web" in completed_tools,
            "fetched_at_least_three_assets": completed_tools.count("fetch_ppt_asset") >= 3,
            "used_three_distinct_source_domains": len(source_domains) >= 3,
            "published_ppt": "publish_ppt_artifact" in completed_tools,
            "one_pptx_only": len(pptx_rows) == 1 and not zip_rows,
            "unique_requested_filename": (
                len(pptx_rows) == 1
                and str(pptx_rows[0].get("filename") or "") == deliverable_name
            ),
            "artifact_receipt_matches": bool(receipt_ids & {str(row["id"]) for row in pptx_rows}),
            "four_slides": slide_count == 4,
            "three_large_embedded_images": len(substantive) >= 3,
            "three_unique_embedded_images": len(unique_substantive) >= 3,
        }
        for name, passed in checks.items():
            print(f"  {'PASS' if passed else 'FAIL'} {name}", flush=True)
        ok = all(checks.values())
        print("PASS" if ok else "FAIL", flush=True)
        return 0 if ok else 1
    finally:
        for file_id in new_file_ids:
            try:
                await user_file_service.delete_file(UID, file_id)
                print(f"[cleanup] deleted user file {file_id}", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"[cleanup] file {file_id}: {exc}", flush=True)
        if thread_id:
            try:
                await harness_orchestrator.delete_thread(UID, thread_id)
                print(f"[cleanup] deleted thread {thread_id}", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"[cleanup] thread {thread_id}: {exc}", flush=True)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
