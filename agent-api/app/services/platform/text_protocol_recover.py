"""Recover text-protocol tool calls into native OpenAI tool_calls (v2.65)."""
from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_KNOWN_TOOLS = {
    "update_plan", "use_skill", "search_web", "bash", "write_file", "edit_file",
    "create_file", "read_file", "glob", "list_files", "download_url",
    "browser_fetch", "browser_open", "browser_act", "browser_close",
    "fetch_tool_result", "ask_user_choice",
}
_KNOWN_TOOL_PATTERN = "|".join(re.escape(name) for name in sorted(_KNOWN_TOOLS))


def _json_tool_envelopes(text: str) -> list[tuple[str, Any]]:
    """Parse complete call envelopes, including prose prefixes but not report examples."""
    source = str(text or "").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*\n?(.*?)\n?```", source, re.I | re.S)
    if fenced:
        source = fenced.group(1).strip()
    try:
        values = [json.loads(source)]
    except (TypeError, ValueError):
        # A model can announce an action before its leaked envelope. Decode
        # complete objects at their actual boundaries; a quoted report example
        # must not become an executable tool call merely because its name matches.
        source = re.sub(r"```.*?```|`[^`\n]*`", "", source, flags=re.S)
        values = []
        decoder = json.JSONDecoder()
        position = 0
        while match := re.search(r"[\[{]", source[position:]):
            start = position + match.start()
            try:
                value, position = decoder.raw_decode(source, start)
            except ValueError:
                position = start + 1
                continue
            values.append(value)
    candidates = []
    for value in values:
        if isinstance(value, dict) and isinstance(value.get("tool_calls"), list):
            candidates.extend(value["tool_calls"])
        elif isinstance(value, dict) and isinstance(value.get("function_call"), dict):
            candidates.append(value["function_call"])
        else:
            candidates.extend(value if isinstance(value, list) else [value])
    result = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        function = candidate.get("function") if isinstance(candidate.get("function"), dict) else candidate
        name = str(function.get("name") or "")
        if name not in _KNOWN_TOOLS:
            continue
        for key in ("arguments", "parameters", "args", "input"):
            if key in function:
                result.append((name, function[key]))
                break
    return result


def _coerce_args(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        args = dict(raw)
    elif isinstance(raw, str):
        s = raw.strip()
        if not s:
            return {}
        try:
            parsed = json.loads(s)
            args = dict(parsed) if isinstance(parsed, dict) else {"value": parsed}
        except Exception:
            return {"_raw": s[:4000]}
    else:
        return {}
    if "steps" not in args and isinstance(args.get("plan"), list):
        args["steps"] = args.pop("plan")
    return args


def _make_tool_call(name: str, args: Dict[str, Any], idx: int = 0) -> Dict[str, Any]:
    return {
        "id": f"recovered_{uuid.uuid4().hex[:12]}_{idx}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(args, ensure_ascii=False),
        },
    }


def _extract_first_json_object(text: str) -> Optional[Any]:
    s = str(text or "").lstrip()
    if not s.startswith("{"):
        return None
    depth = 0
    in_str = False
    esc = False
    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(s[: i + 1])
                except Exception:
                    return None
    return None


def looks_like_text_tool_payload(text: str) -> bool:
    t = str(text or "")
    if not t.strip():
        return False
    if re.search(rf"<\s*({_KNOWN_TOOL_PATTERN}|function_call|tool_call)\b", t, re.I):
        return True
    if re.search(r"invoke\s+name\s*=\s*[\"']\w+", t, re.I):
        return True
    if _json_tool_envelopes(t):
        return True
    if "|DSML|" in t or "｜DSML｜" in t or "tool_calls_begin" in t:
        return True
    return False


def recover_text_tool_calls(text: str) -> List[Dict[str, Any]]:
    """Parse leaked text tool protocol into native tool_calls; empty on failure."""
    src = str(text or "")
    if not src.strip():
        return []
    recovered: List[Dict[str, Any]] = []

    # 1) XML tag + JSON object: <update_plan>{...}
    for m in re.finditer(
        rf"<({_KNOWN_TOOL_PATTERN})\b[^>]*>\s*(\{{)",
        src,
        re.I | re.S,
    ):
        name = m.group(1).strip().lower()
        args = _extract_first_json_object(src[m.start(2):])
        if args is not None:
            recovered.append(_make_tool_call(name, _coerce_args(args), len(recovered)))

    # 2) DSML / invoke: prefer parameter tags, then bare JSON
    if not recovered:
        for m in re.finditer(r"invoke\s+name\s*=\s*[\"']([a-zA-Z_][\w]*)[\"']", src, re.I):
            name = m.group(1).strip()
            if name not in _KNOWN_TOOLS:
                continue
            tail = src[m.end(): m.end() + 8000]
            args: Dict[str, Any] = {}
            for pm in re.finditer(
                r"parameter\s+name\s*=\s*[\"']([\w]+)[\"'][^>]*>\s*([^<]+)",
                tail,
                re.I | re.S,
            ):
                key = pm.group(1).strip()
                raw_val = (pm.group(2) or "").strip()
                try:
                    args[key] = json.loads(raw_val)
                except Exception:
                    args[key] = raw_val
            if args:
                recovered.append(_make_tool_call(name, _coerce_args(args), len(recovered)))
                continue
            brace = tail.find("{")
            obj = _extract_first_json_object(tail[brace:]) if brace >= 0 else None
            if obj is not None:
                recovered.append(_make_tool_call(name, _coerce_args(obj), len(recovered)))

    # 3) JSON envelope {"name":"update_plan","arguments":{...}}
    if not recovered:
        for name, args in _json_tool_envelopes(src):
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    continue
            if not isinstance(args, dict):
                continue
            recovered.append(_make_tool_call(name, _coerce_args(args), len(recovered)))

    seen = set()
    out: List[Dict[str, Any]] = []
    for tc in recovered:
        key = (
            str((tc.get("function") or {}).get("name") or ""),
            str((tc.get("function") or {}).get("arguments") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(tc)
    if out:
        logger.info(
            "文本工具协议已恢复为原生 tool_calls: %s",
            [((c.get("function") or {}).get("name")) for c in out],
        )
    return out
