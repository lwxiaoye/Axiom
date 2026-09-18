"""skill_package_bridge 回归：一方 ppt-studio 包适配、取包缓存、zip 解包与完整性事实。

2026-09-18 起取包在进程内完成（skill_catalog：内置包读磁盘 / 导入包解 zip），Java
`/ai/skill/files|file|package` 通道连同 `_flatten_tree`、文本/二进制通道判定一起删除，
对应的用例也一并移除；这里只保留与通道无关的行为。
"""
import unittest
from unittest.mock import patch

from app.services.skills import skill_package_bridge as bridge
from app.services.skills.skill_package_bridge import (
    adapt_first_party_skill_package,
    normalize_skill_instructions,
)


class FirstPartyPackageAdaptationTests(unittest.TestCase):
    def test_ppt_studio_gets_repository_export_wrapper(self):
        files = {
            "SKILL.md": b"ppt",
            "scripts/export_pptx.py": b"print('export')",
        }
        adapted = adapt_first_party_skill_package("ppt-studio", files)

        self.assertIn("scripts/run_export.py", adapted)
        self.assertIn(b"export_pptx.py", adapted["scripts/run_export.py"])
        self.assertNotIn("scripts/run_export.py", files, "输入包不得被就地篡改")
        wasm = adapted["scripts/local-export/pptd_wasm_bg.wasm"]
        self.assertGreater(len(wasm), 1000)
        self.assertTrue(wasm.startswith(b"\x00asm"))
        overlay = adapted["scripts/local-export/normalize-theme-styles.mjs"]
        self.assertIn(b"element.content.style = element.style", overlay)
        self.assertIn(b"type: 'solid'", overlay)
        exporter = adapted["scripts/local-export/export-pptd.mjs"]
        self.assertIn(b"normalizeFill(page.background)", exporter)
        self.assertIn("skill.json", adapted)

    def test_ppt_studio_keeps_an_existing_wasm_blob(self):
        files = {
            "SKILL.md": b"ppt",
            "scripts/export_pptx.py": b"print('export')",
            "scripts/local-export/pptd_wasm_bg.wasm": b"\x00asmFAKE",
        }
        adapted = adapt_first_party_skill_package("ppt-studio", files)
        self.assertEqual(adapted["scripts/local-export/pptd_wasm_bg.wasm"], b"\x00asmFAKE")

    def test_ppt_studio_overlays_stale_export_wrapper(self):
        adapted = adapt_first_party_skill_package(
            "ppt-studio",
            {
                "SKILL.md": b"open-kimi-ppt PPTD",
                "scripts/export_pptx.py": b"print('export')",
                "scripts/run_export.py": b"print('stale wrapper')",
            },
        )
        self.assertNotEqual(adapted["scripts/run_export.py"], b"print('stale wrapper')")
        self.assertIn(b"_normalize_project", adapted["scripts/run_export.py"])

    def test_injected_wasm_is_removed_from_unmounted_binary_list(self):
        cleaned = bridge._drop_injected_ppt_engine_from_unmounted({
            "binary": ["scripts/local-export/pptd_wasm_bg.wasm", "media/cover.png"],
            "fetch_failed": ["skill.json", "reference/missing.md"],
        })
        self.assertEqual(cleaned["binary"], ["media/cover.png"])
        self.assertEqual(cleaned["fetch_failed"], ["reference/missing.md"])

    def test_unrelated_skill_is_unchanged(self):
        files = {"scripts/export_pptx.py": b"x"}
        self.assertEqual(adapt_first_party_skill_package("other", files), files)

    def test_ppt_instructions_override_runtime_probes_and_mandatory_npx_reminder(self):
        original = (
            "open-kimi-ppt PPTD\n"
            "10. After completing and delivering any presentation, always end the final response "
            "with `npx open-kimi-ppt-skill serve` to edit.\n"
        )

        normalized = normalize_skill_instructions(original, skill_name="ppt-studio")

        self.assertNotIn("always end the final response", normalized)
        self.assertIn("Do not probe", normalized)
        self.assertIn("Never copy them into public progress or the final answer", normalized)
        self.assertIn("unless the user explicitly asks", normalized)

    def test_legacy_chromium_export_gate_is_rewritten(self):
        original = (
            "open-kimi-ppt PPTD\n"
            "### step4. PPT validation\n"
            "Do not export the PPTX until the visual review passes.\n"
            "Then run npx open-kimi-ppt-skill serve.\n"
        )
        normalized = normalize_skill_instructions(original, skill_name="ppt-studio")
        self.assertNotIn("Do not export the PPTX until the visual review passes", normalized)
        self.assertIn("publish_ppt_artifact", normalized)
        self.assertIn("run_export.py", normalized)
        self.assertNotIn("npx open-kimi-ppt-skill serve", normalized)

    def test_ppt_step0_is_replaced_in_the_final_composed_skill_block(self):
        from app.services.chat.turn_context_builder import _format_skill_block

        block = _format_skill_block({
            "id": "opaque-skill-id",
            "name": "ppt-studio",
            "description": "open-kimi-ppt PPTD authoring",
            "instructions": (
                "## PPT production workflow\n\n"
                "### Step 0. Check the environment\n"
                "Run `node --version`, `npm --version`, and `npx --version` before authoring.\n\n"
                "### step1. Read the context thoroughly\nContinue with authoring.\n"
            ),
        })

        self.assertNotIn("node --version", block)
        self.assertNotIn("npm --version", block)
        self.assertNotIn("先在沙箱内检查", block)
        self.assertIn("already validated and mounted", block)
        self.assertIn("不要再次", block)

    def test_third_party_instructions_are_not_globally_rewritten_or_overlaid(self):
        from app.services.chat.turn_context_builder import _format_skill_block

        original = "Call create_file(path='deck.txt') exactly as documented."
        block = _format_skill_block({
            "id": "third-party",
            "name": "community-slides",
            "description": "Third-party slide helper",
            "instructions": original,
        })

        self.assertIn(original, block)
        self.assertNotIn("平台执行适配", block)

    def test_legacy_ppt_documentation_is_normalized_before_validation(self):
        retired_run = "run" + "_code"
        retired_create = "create" + "_file"
        retired_update = "update" + "_file"
        files = {
            "SKILL.md": (
                "open-kimi-ppt PPTD\n"
                f"Use {retired_run}, {retired_create}, and {retired_update}; "
                "read /workspace/inputs/a and write /workspace/outputs/deck.pptx."
            ).encode(),
            "scripts/export_pptx.py": b"print('export')",
        }

        adapted = adapt_first_party_skill_package("ppt-studio", files)

        self.assertEqual(bridge.validate_harness_skill_package(adapted), ())
        instructions = adapted["SKILL.md"].decode()
        self.assertIn("fetch_ppt_asset", instructions)
        self.assertIn("media/<filename>", instructions)
        self.assertNotIn(retired_run, instructions)
        self.assertNotIn("/workspace/inputs", instructions)

    def test_legacy_ppt_executable_is_not_silently_rewritten(self):
        retired_run = "run" + "_code"
        script = f"result = {retired_run}('build')".encode()

        adapted = adapt_first_party_skill_package(
            "ppt-studio",
            {"SKILL.md": b"open-kimi-ppt PPTD", "scripts/build.py": script},
        )

        self.assertEqual(adapted["scripts/build.py"], script)
        self.assertIn(retired_run, bridge.validate_harness_skill_package(adapted))

    def test_incompatible_third_party_documentation_is_rejected_not_translated(self):
        retired_create = "create" + "_file"
        files = {"SKILL.md": f"Call {retired_create}(path='x')".encode()}

        adapted = adapt_first_party_skill_package("community-writer", files)

        self.assertEqual(adapted, files)
        self.assertIn(retired_create, bridge.validate_harness_skill_package(adapted))


class PackageFetchCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        bridge._package_fetch_cache.clear()
        bridge._package_fetch_inflight.clear()

    async def asyncTearDown(self):
        bridge._package_fetch_cache.clear()
        bridge._package_fetch_inflight.clear()

    async def test_same_user_and_record_reuses_successful_package_without_shared_mutation(self):
        calls = 0

        async def fake_fetch(_record_id, _token):
            nonlocal calls
            calls += 1
            return {
                "files": {"SKILL.md": b"content"},
                "entrypoint": None,
                "error": None,
                "declared_scripts": False,
                "unmounted": {},
            }

        with patch.object(bridge, "_fetch_one_skill", fake_fetch):
            first = await bridge._fetch_one_skill_cached("rec-1", "token-a")
            first["files"].clear()
            second = await bridge._fetch_one_skill_cached("rec-1", "token-a")
            await bridge._fetch_one_skill_cached("rec-1", "token-b")

        self.assertEqual(calls, 2, "同一 ACL 复用缓存，不同 token 必须重新取包")
        self.assertEqual(second["files"], {"SKILL.md": b"content"})

    async def test_partial_fetch_failure_is_not_cached_and_retries(self):
        calls = 0

        async def fake_fetch(_record_id, _token):
            nonlocal calls
            calls += 1
            if calls == 1:
                return {
                    "files": {"SKILL.md": b"partial"},
                    "entrypoint": None,
                    "error": None,
                    "declared_scripts": True,
                    "unmounted": {"fetch_failed": ["scripts/build.py"]},
                }
            return {
                "files": {"SKILL.md": b"complete", "scripts/build.py": b"print('ok')"},
                "entrypoint": None,
                "error": None,
                "declared_scripts": True,
                "unmounted": {"fetch_failed": []},
            }

        with patch.object(bridge, "_fetch_one_skill", fake_fetch):
            first = await bridge._fetch_one_skill_cached("rec-1", "token-a")
            second = await bridge._fetch_one_skill_cached("rec-1", "token-a")
            third = await bridge._fetch_one_skill_cached("rec-1", "token-a")

        self.assertEqual(first["unmounted"]["fetch_failed"], ["scripts/build.py"])
        self.assertEqual(second["files"]["scripts/build.py"], b"print('ok')")
        self.assertEqual(third["files"], second["files"])
        self.assertEqual(calls, 2)


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buf.getvalue()


class PackageIntegrityAndZipTests(unittest.IsolatedAsyncioTestCase):
    def test_text_json_channel_keeps_binaries_unmounted_and_incomplete(self):
        integrity = bridge.package_integrity(
            {"SKILL.md": b"# doc"},
            {"binary": ["assets/cover.png"], "over_file_limit": [], "over_byte_budget": [], "fetch_failed": []},
            channel="text_json",
        )
        self.assertEqual(integrity["status"], "incomplete")
        self.assertEqual(integrity["channel"], "text_json")
        self.assertEqual(integrity["unmounted"]["binary"], ["assets/cover.png"])

    def test_complete_zip_package_has_digest(self):
        integrity = bridge.package_integrity(
            {"SKILL.md": b"# doc", "assets/cover.png": b"\x89PNG\r\n"},
            {"binary": [], "fetch_failed": []},
            channel="zip",
        )
        self.assertEqual(integrity["status"], "complete")
        self.assertEqual(integrity["file_count"], 2)
        self.assertTrue(integrity["digest"])

    def test_zip_extract_keeps_png_bytes(self):
        raw = _zip_bytes({
            "writer/SKILL.md": b"# report",
            "writer/templates/notice.docx": b"PK\x03\x04DOCX",
        })
        extracted = bridge._extract_zip_package(raw)
        self.assertEqual(extracted["channel"], "zip")
        self.assertEqual(extracted["files"]["SKILL.md"], b"# report")
        self.assertEqual(extracted["files"]["templates/notice.docx"], b"PK\x03\x04DOCX")
        self.assertEqual(extracted["unmounted"]["binary"], [])

    async def test_in_process_source_is_attached_with_integrity(self):
        """进程内取包：skill_catalog 给出的文件树原样挂载并附完整性事实。"""
        png = b"\x89PNG\r\n\x1a\n" + b"x" * 24

        async def fake_source(_record_id, _token):
            return {
                "files": {"SKILL.md": b"# builtin", "cover.png": png},
                "entrypoint": None, "error": None, "declared_scripts": False,
                "unmounted": {"binary": [], "over_file_limit": [], "over_byte_budget": [], "fetch_failed": []},
                "channel": "builtin",
            }

        with patch("app.services.skills.skill_catalog.load_skill_package_source", fake_source):
            fetched = await bridge._fetch_one_skill("rec-1", "tok")
        self.assertEqual(fetched["channel"], "builtin")
        self.assertEqual(fetched["files"]["cover.png"], png)  # 二进制字节不再经 JSON 通道，原样保留
        self.assertEqual(fetched["integrity"]["status"], "complete")

    async def test_missing_record_is_an_error_result_not_an_exception(self):
        """目录里没有这条记录：返回带 error 的结果（调用方据此占位），declared_scripts 未知。"""
        async def fake_source(_record_id, _token):
            return None

        with patch("app.services.skills.skill_catalog.load_skill_package_source", fake_source):
            fetched = await bridge._fetch_one_skill("gone", "tok")
        self.assertEqual(fetched["files"], {})
        self.assertIn("没有这条记录", fetched["error"])
        self.assertIsNone(fetched["declared_scripts"])
        self.assertEqual(fetched["integrity"]["status"], "unavailable")

    async def test_source_exception_is_reported_not_raised(self):
        async def fake_source(_record_id, _token):
            raise RuntimeError("mysql gone away")

        with patch("app.services.skills.skill_catalog.load_skill_package_source", fake_source):
            fetched = await bridge._fetch_one_skill("rec-1", "tok")
        self.assertEqual(fetched["files"], {})
        self.assertIn("mysql gone away", fetched["error"])
        self.assertIsNone(fetched["declared_scripts"])


if __name__ == "__main__":
    unittest.main()
