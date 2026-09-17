#!/usr/bin/env python3
"""Download a JSON name->URL image map concurrently with bounded failures."""
from __future__ import annotations

import argparse
import json
import ssl
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Sequence

from PIL import Image


def _fetch(name: str, url: str, output_dir: Path, timeout: float, insecure: bool) -> str:
    target = output_dir / Path(name).name
    request = urllib.request.Request(url, headers={"User-Agent": "ppt-studio/2.5"})
    context = ssl._create_unverified_context() if insecure else ssl.create_default_context()
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        data = response.read()
    target.write_bytes(data)
    with Image.open(target) as image:
        image.verify()
    with Image.open(target) as image:
        width, height = image.size
    if width < 800 or height < 500:
        target.unlink(missing_ok=True)
        raise RuntimeError(f"resolution too small: {width}x{height}")
    return f"{target.name} {width}x{height} {len(data) // 1024}KB"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch slide images concurrently")
    parser.add_argument("map", type=Path, help="JSON object: filename -> https URL")
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--allow-insecure-fallback", action="store_true")
    args = parser.parse_args(argv)
    items = json.loads(args.map.read_text(encoding="utf-8"))
    if not isinstance(items, dict) or not items:
        raise SystemExit("image map must be a non-empty JSON object")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    failures = []
    results = []
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 8))) as pool:
        futures = {
            pool.submit(_fetch, str(name), str(url), args.output_dir, args.timeout, False): (name, url)
            for name, url in items.items()
        }
        for future in as_completed(futures):
            name, url = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001
                failures.append((str(name), str(url), str(exc)))

    if failures and args.allow_insecure_fallback:
        retry_failures = []
        for name, url, _error in failures:
            try:
                results.append(_fetch(name, url, args.output_dir, args.timeout, True))
            except Exception as exc:  # noqa: BLE001
                retry_failures.append((name, url, str(exc)))
        failures = retry_failures

    for row in sorted(results):
        print(f"OK {row}")
    for name, _url, error in failures:
        print(f"FAIL {name}: {error}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
