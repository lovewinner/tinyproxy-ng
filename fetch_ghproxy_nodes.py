#!/usr/bin/env python3
"""Fetch GitHub mirror node list from github.akams.cn and save locally.

The site renders nodes as a Next.js SSR app.  The node list is embedded in
one of the JS chunks under /_next/static/chunks/.  This script walks every
chunk, extracts the ``let j=[{...}]`` array literal, parses it as JSON, and
writes the result to a local JSON file.

Usage:
    python fetch_ghproxy_nodes.py                          # default output
    python fetch_ghproxy_nodes.py -o nodes.json            # custom output
    python fetch_ghproxy_nodes.py -s https://github.akams.cn  # custom source
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time

import aiohttp

_DEFAULT_SOURCE = "https://github.akams.cn"
_DEFAULT_OUTPUT = "ghproxy_nodes.json"

_CHUNK_RE = re.compile(r"/_next/static/chunks/([a-f0-9]+)\.js")
_NODES_RE = re.compile(r"let\s+j\s*=\s*(\[[^;]*?\])", re.DOTALL)


async def fetch_page(session: aiohttp.ClientSession, url: str) -> str:
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
        return await resp.text()


async def fetch_chunk(session: aiohttp.ClientSession, base: str, chunk_id: str) -> str:
    url = f"{base.rstrip('/')}/_next/static/chunks/{chunk_id}.js"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
        return await resp.text()


def _js_to_json(js_array: str) -> str:
    """Convert a JS ``[{label:...,value:...}]`` literal to valid JSON."""
    js_array = re.sub(r'(?<={)\s*label\s*:', '"label":', js_array)
    js_array = re.sub(r',\s*value\s*:', ',"value":', js_array)
    return js_array


def extract_nodes(chunk_text: str) -> list[dict]:
    """Try to find a ``let j = [{...}]`` array in the chunk text."""
    match = _NODES_RE.search(chunk_text)
    if not match:
        return []
    js_array = _js_to_json(match.group(1))
    return json.loads(js_array)


async def main():
    parser = argparse.ArgumentParser(description="Fetch GH mirror node list")
    parser.add_argument("-s", "--source", default=_DEFAULT_SOURCE,
                        help=f"Source URL (default: {_DEFAULT_SOURCE})")
    parser.add_argument("-o", "--output", default=_DEFAULT_OUTPUT,
                        help=f"Output JSON file (default: {_DEFAULT_OUTPUT})")
    args = parser.parse_args()

    fetched_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(f"[{fetched_at}] Fetching page: {args.source}")

    async with aiohttp.ClientSession() as session:
        html = await fetch_page(session, args.source)
        chunk_ids = [m.group(1) for m in _CHUNK_RE.finditer(html)]
        chunk_ids = list(dict.fromkeys(chunk_ids))
        print(f"  Found {len(chunk_ids)} unique chunk(s)")

        nodes = []
        for cid in chunk_ids:
            try:
                js = await fetch_chunk(session, args.source, cid)
            except Exception as exc:
                print(f"  chunk {cid}: fetch failed ({exc}), skip")
                continue
            found = extract_nodes(js)
            if found:
                nodes = found
                print(f"  chunk {cid}: extracted {len(nodes)} node(s)")
                break
            print(f"  chunk {cid}: no node list")

    if not nodes:
        print("ERROR: Could not locate node list in any chunk.", file=sys.stderr)
        sys.exit(1)

    data = {
        "fetched_at": fetched_at,
        "source_url": args.source,
        "nodes": nodes,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(nodes)} nodes to {args.output}")


if __name__ == "__main__":
    import asyncio
    try:
        asyncio.run(main())
    finally:
        import gc
        gc.collect()