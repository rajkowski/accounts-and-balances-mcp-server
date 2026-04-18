#!/usr/bin/env python3
# Copyright 2026 Matt Rajkowski
# SPDX-License-Identifier: Apache-2.0
"""Basic smoke test for the Accounts MCP server over stdio.

This script starts the MCP server process, performs MCP initialization,
then calls a couple of read-only tools to verify end-to-end connectivity.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _extract_json_compatible_result(result: Any) -> Any:
    """Return the most useful structured payload from a CallToolResult."""
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured

    content = getattr(result, "content", None)
    if not content:
        return None

    if len(content) == 1 and getattr(content[0], "type", None) == "text":
        text = getattr(content[0], "text", "")
        if isinstance(text, str):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text

    fallback: list[dict[str, Any]] = []
    for block in content:
        block_type = getattr(block, "type", "unknown")
        entry: dict[str, Any] = {"type": block_type}
        if hasattr(block, "text"):
            entry["text"] = getattr(block, "text")
        fallback.append(entry)
    return fallback


def _expect_list_payload(result: Any, tool_name: str) -> list[Any]:
    """Validate a tool response and return a list payload."""
    if getattr(result, "isError", False):
        raise RuntimeError(f"{tool_name} returned an error result: {result!r}")

    payload = _extract_json_compatible_result(result)
    if isinstance(payload, dict) and isinstance(payload.get("result"), list):
        return payload["result"]
    if not isinstance(payload, list):
        raise RuntimeError(f"{tool_name} returned unexpected payload: {payload!r}")
    return payload


def _assert_no_embedded_error(payload: list[Any], tool_name: str) -> None:
    """Fail when a tool encodes an error object in a successful list result."""
    if payload and isinstance(payload[0], dict) and isinstance(payload[0].get("error"), str):
        raise RuntimeError(f"{tool_name} returned embedded error: {payload[0]['error']}")


async def _run_smoke_test(server_command: str, server_script: str) -> int:
    server_params = StdioServerParameters(
        command=server_command,
        args=[server_script],
        cwd=str(Path(server_script).resolve().parent),
        env={
            **os.environ,
            "PYTHONUNBUFFERED": "1",
        },
    )

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            tools_result = await session.list_tools()
            tool_names = sorted(tool.name for tool in tools_result.tools)
            print(f"Server initialized. Tools available: {', '.join(tool_names)}")

            folders_result = await session.call_tool("list_folders", {})
            folders_payload = _expect_list_payload(folders_result, "list_folders")

            print(f"list_folders ok: {len(folders_payload)} folder(s)")

            accounts_result = await session.call_tool("list_accounts", {})
            accounts_payload = _expect_list_payload(accounts_result, "list_accounts")

            print(f"list_accounts ok: {len(accounts_payload)} account(s)")

            if folders_payload:
                first = folders_payload[0]
                name = first.get("name") if isinstance(first, dict) else None
                if isinstance(name, str) and name:
                    accounts_in_folder_result = await session.call_tool(
                        "list_accounts",
                        {"folder_name": name},
                    )
                    accounts_in_folder_payload = _expect_list_payload(
                        accounts_in_folder_result,
                        "list_accounts(folder_name=...)",
                    )
                    print(
                        "list_accounts(folder_name=...) ok: "
                        f"{len(accounts_in_folder_payload)} account(s) in '{name}'"
                    )

            projection_result = await session.call_tool(
                "project_balance",
                {"account_name": "Checking", "days": 30},
            )
            projection_payload = _expect_list_payload(projection_result, "project_balance(Checking)")
            _assert_no_embedded_error(projection_payload, "project_balance(Checking)")
            if not projection_payload:
                raise RuntimeError("project_balance(Checking) returned an empty projection")

            first_day = projection_payload[0]
            if not isinstance(first_day, dict) or "date" not in first_day or "balance" not in first_day:
                raise RuntimeError(
                    "project_balance(Checking) returned unexpected row shape; "
                    "expected keys including 'date' and 'balance'"
                )

            print(f"project_balance(Checking) ok: {len(projection_payload)} day(s)")

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test for Accounts MCP server.")
    parser.add_argument(
        "--server-command",
        default=sys.executable,
        help="Executable used to run the MCP server (default: current Python executable).",
    )
    parser.add_argument(
        "--server-script",
        default=str(Path(__file__).with_name("accounts_mcp.py")),
        help="Path to the MCP server script (default: accounts_mcp.py next to this file).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(_run_smoke_test(args.server_command, args.server_script))
    except Exception as exc:  # pragma: no cover - best-effort smoke test output
        print(f"ERROR: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
