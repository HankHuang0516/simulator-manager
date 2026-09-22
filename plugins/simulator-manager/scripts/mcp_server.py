#!/usr/bin/env python3
"""Dependency-free MCP bridge for the simulator-manager CLI."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

PROTOCOL_VERSION = "2025-03-26"
MAX_CAPTURE = 32768

TOOLS = [
    {
        "name": "simulator_manager_status",
        "description": "Read the shared queue, leases, environments, deadlines, watcher, and host-pressure state. This never acquires or stops a device.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "simulator_manager_enable",
        "description": "Register this Codex session and project for shared simulator coordination. Optionally prepare manager-owned fallback devices from installed SDK components.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {"type": "string", "minLength": 1},
                "project": {"type": "string", "minLength": 1},
                "prepare": {"type": "boolean", "default": False},
            },
            "required": ["session", "project"],
            "additionalProperties": False,
        },
    },
    {
        "name": "simulator_manager_run",
        "description": "Acquire through FIFO admission, optionally boot the assigned device, run one argv command under the total occupancy deadline, and always release without powering off the simulator/emulator. Explicit shutdown actions are rejected. Use only after build/unit checks show runtime or UI validation is needed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "platform": {"type": "string", "enum": ["ios", "android", "gui"]},
                "session": {"type": "string", "minLength": 1},
                "project": {"type": "string", "minLength": 1},
                "command": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                "boot": {"type": "boolean", "default": True},
                "foreground": {"type": "boolean", "default": False},
                "queue_timeout_seconds": {"type": "number", "minimum": 0, "maximum": 900, "default": 300},
                "command_timeout_seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": 600, "default": 600},
                "budget_seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": 600, "default": 600},
                "restartable": {"type": "boolean", "default": False},
                "max_requeues": {"type": "integer", "minimum": 0, "maximum": 3, "default": 0},
            },
            "required": ["platform", "session", "project", "command"],
            "additionalProperties": False,
        },
    },
    {
        "name": "simulator_manager_cleanup",
        "description": "Run safe stale-state maintenance. It preserves live owners, active work, leased devices, external devices, and private environment data.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "simulator_manager_ui",
        "description": "Open or focus the single native macOS floating dashboard for queue, lease, environment, and pressure monitoring.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "simulator_manager_doctor",
        "description": "Check the installed CLI version, configuration, shared state, and optional iOS/Android SDK commands without acquiring a simulator.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "simulator_manager_guidance",
        "description": "Audit this registered Codex task for direct simulator/emulator commands outside managed leases. Return targeted teaching steps without stopping any task, device, or adb process. Explain every returned finding to the user before the next runtime/UI action.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {"type": "string", "minLength": 1},
                "acknowledge": {"type": "boolean", "default": False},
            },
            "required": ["session"],
            "additionalProperties": False,
        },
    },
]


class ToolError(RuntimeError):
    pass


def cli_path() -> str:
    override = os.environ.get("SIM_MANAGER_CLI")
    if override:
        return str(Path(override).expanduser())
    installed = Path.home() / ".local/bin/sim-manager"
    if installed.is_file():
        return str(installed)
    found = shutil.which("sim-manager")
    if found:
        return found
    local = Path(__file__).resolve().parents[3] / "bin/sim-manager"
    if local.is_file():
        return str(local)
    raise ToolError("sim-manager CLI is not installed. Run the official installer first.")


def _number(value: Any, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= float(value) <= high:
        raise ToolError(f"{name} must be between {low:g} and {high:g}")
    return float(value)


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"{name} must be a non-empty string")
    return value


def _reject_runtime_shutdown(command: list[str]) -> None:
    rendered = " ".join(command)
    forbidden = (
        (r"\bsimctl\b[^\n;&|]*\bshutdown\b", "simctl shutdown"),
        (r"\badb\b[^\n;&|]*\bemu\s+kill\b", "adb emu kill"),
    )
    for pattern, action in forbidden:
        if re.search(pattern, rendered, re.IGNORECASE):
            raise ToolError(
                f"command contains forbidden `{action}`; finish testing and release "
                "use rights while leaving the assigned runtime warm"
            )


def run_cli(args: list[str], timeout: float = 30) -> dict[str, Any]:
    env = os.environ.copy()
    state = env.get("SIM_MANAGER_STATE_DIR")
    argv = [cli_path(), *args]
    if state:
        insertion = argv.index("--") if "--" in argv else len(argv)
        argv[insertion:insertion] = ["--state-dir", state]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env, shell=False)
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"sim-manager did not finish within {timeout:g} seconds") from exc
    stdout, stderr = result.stdout[-MAX_CAPTURE:], result.stderr[-MAX_CAPTURE:]
    parsed: Any = None
    if stdout.strip():
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            parsed = {"stdout": stdout.strip()}
    if result.returncode != 0:
        detail = parsed.get("error") if isinstance(parsed, dict) else None
        raise ToolError(detail or stderr.strip() or f"sim-manager exited with {result.returncode}")
    payload = parsed if isinstance(parsed, dict) else {"result": parsed}
    if stderr.strip():
        payload["workload_output"] = stderr.strip()
    return payload


def invoke_tool(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    a = arguments or {}
    if not isinstance(a, dict):
        raise ToolError("tool arguments must be an object")
    if name == "simulator_manager_status":
        return run_cli(["status", "--json"])
    if name == "simulator_manager_cleanup":
        return run_cli(["cleanup", "--json"])
    if name == "simulator_manager_ui":
        return run_cli(["ui", "--json"], 120)
    if name == "simulator_manager_enable":
        session = _text(a.get("session"), "session")
        project = str(Path(_text(a.get("project"), "project")).expanduser().resolve())
        argv = ["enable", "--session", session, "--project", project, "--owner-pid", str(os.getpid())]
        if a.get("prepare", False):
            argv.append("--prepare")
        argv.append("--json")
        enabled = run_cli(argv, 300 if a.get("prepare", False) else 30)
        enabled["guidance"] = run_cli(["audit", "--session", session, "--json"])
        return enabled
    if name == "simulator_manager_run":
        platform = a.get("platform")
        if platform not in ("ios", "android", "gui"):
            raise ToolError("platform must be ios, android, or gui")
        session = _text(a.get("session"), "session")
        project = str(Path(_text(a.get("project"), "project")).expanduser().resolve())
        command = a.get("command")
        if not isinstance(command, list) or not command or any(not isinstance(v, str) or not v for v in command):
            raise ToolError("command must be a non-empty array of non-empty argv strings")
        _reject_runtime_shutdown(command)
        queue_timeout = _number(a.get("queue_timeout_seconds", 300), "queue_timeout_seconds", 0, 900)
        command_timeout = _number(a.get("command_timeout_seconds", 600), "command_timeout_seconds", 0.001, 600)
        budget = _number(a.get("budget_seconds", 600), "budget_seconds", 0.001, 600)
        max_requeues = a.get("max_requeues", 0)
        if isinstance(max_requeues, bool) or not isinstance(max_requeues, int) or not 0 <= max_requeues <= 3:
            raise ToolError("max_requeues must be an integer from 0 to 3")
        argv = ["run", platform, "--session", session, "--project", project, "--mode", "auto",
                "--timeout", str(queue_timeout), "--command-timeout", str(command_timeout),
                "--budget-seconds", str(budget)]
        if a.get("boot", True) and platform != "gui":
            argv.extend(["--boot", "--boot-timeout", str(min(180.0, budget))])
        if a.get("foreground", False) or platform == "gui":
            argv.append("--foreground")
        if a.get("restartable", False):
            argv.extend(["--requeue-on-yield", "--max-requeues", str(max_requeues)])
        elif max_requeues:
            raise ToolError("max_requeues requires restartable=true")
        argv.extend(["--json", "--", *command])
        return run_cli(argv, queue_timeout + budget * (max_requeues + 1) + 45)
    if name == "simulator_manager_doctor":
        path = cli_path()
        version = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10, shell=False)
        return {
            "cli": path,
            "version": version.stdout.strip(),
            "config": run_cli(["validate-config", "--json"]),
            "state": run_cli(["status", "--json"]),
            "optional_tools": {tool: shutil.which(tool) for tool in ("xcrun", "adb", "emulator")},
        }
    if name == "simulator_manager_guidance":
        session = _text(a.get("session"), "session")
        argv = ["audit", "--session", session]
        if a.get("acknowledge", False):
            argv.append("--acknowledge")
        argv.append("--json")
        return run_cli(argv)
    raise ToolError(f"Unknown tool: {name}")


def response_payload(value: dict[str, Any], is_error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, indent=2)}],
        "structuredContent": value,
    }
    if is_error:
        result["isError"] = True
    return result


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    method, request_id = message.get("method"), message.get("id")
    if request_id is None:
        return None
    if method == "initialize":
        result = {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {"listChanged": False}},
                  "serverInfo": {"name": "simulator-manager", "version": "3.4.0"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = message.get("params") or {}
        try:
            result = response_payload(invoke_tool(params.get("name", ""), params.get("arguments")))
        except (ToolError, OSError, ValueError) as exc:
            result = response_payload({"error": str(exc)}, True)
    elif method in ("resources/list", "prompts/list"):
        result = {"resources" if method.startswith("resources") else "prompts": []}
    else:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}}
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def main() -> int:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            reply = handle(json.loads(line))
        except (json.JSONDecodeError, TypeError) as exc:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
