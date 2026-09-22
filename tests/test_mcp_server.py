import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SERVER = Path(__file__).parents[1] / "plugins/simulator-manager/scripts/mcp_server.py"
SPEC = importlib.util.spec_from_file_location("simulator_manager_mcp", SERVER)
MCP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MCP)


class MCPServerTests(unittest.TestCase):
    def test_tool_catalog_has_supervised_runtime_and_read_only_status(self):
        names = {tool["name"] for tool in MCP.TOOLS}
        self.assertEqual(names, {
            "simulator_manager_status", "simulator_manager_enable", "simulator_manager_run",
            "simulator_manager_cleanup", "simulator_manager_ui", "simulator_manager_doctor",
            "simulator_manager_guidance",
        })

    def test_enable_registers_long_lived_mcp_owner_and_returns_guidance(self):
        completed = subprocess.CompletedProcess([], 0, '{"shared_mode":true}\n', "")
        with mock.patch.object(MCP, "cli_path", return_value="/tmp/sim-manager"), \
             mock.patch.object(MCP.subprocess, "run", return_value=completed) as run:
            result = MCP.invoke_tool("simulator_manager_enable", {"session":"task-1","project":"/tmp/app"})
        self.assertTrue(result["shared_mode"])
        self.assertEqual(run.call_count, 2)
        self.assertIn("--owner-pid", run.call_args_list[0].args[0])
        self.assertEqual(run.call_args_list[1].args[0][1:4], ["audit","--session","task-1"])

    def test_run_builds_argv_without_shell(self):
        completed = subprocess.CompletedProcess([], 0, '{"released":true,"exit_code":0}\n', "")
        with mock.patch.object(MCP, "cli_path", return_value="/tmp/sim-manager"), \
             mock.patch.object(MCP.subprocess, "run", return_value=completed) as run:
            result = MCP.invoke_tool("simulator_manager_run", {
                "platform": "ios", "session": "task-1", "project": "/tmp/app",
                "command": ["xcodebuild", "test"], "budget_seconds": 90,
            })
        self.assertTrue(result["released"])
        argv = run.call_args.args[0]
        self.assertEqual(argv[-3:], ["--", "xcodebuild", "test"])
        self.assertIn("--boot", argv)
        self.assertIs(run.call_args.kwargs["shell"], False)

    def test_run_rejects_shell_string_and_unsafe_requeue(self):
        base = {"platform": "android", "session": "s", "project": "/tmp/p"}
        with self.assertRaises(MCP.ToolError):
            MCP.invoke_tool("simulator_manager_run", {**base, "command": "adb devices"})
        with self.assertRaises(MCP.ToolError):
            MCP.invoke_tool("simulator_manager_run", {**base, "command": ["true"], "max_requeues": 1})

    def test_run_rejects_device_shutdown_actions(self):
        base = {"platform":"ios","session":"s","project":"/tmp/p"}
        with self.assertRaisesRegex(MCP.ToolError,"simctl shutdown"):
            MCP.invoke_tool("simulator_manager_run", {
                **base,"command":["sh","-c","xcrun simctl shutdown $SIM_MANAGER_UDID"],
            })
        with self.assertRaisesRegex(MCP.ToolError,"adb emu kill"):
            MCP.invoke_tool("simulator_manager_run", {
                **base,"platform":"android","command":["adb","-s","emulator-5554","emu","kill"],
            })

    def test_custom_state_argument_stays_outside_child_command(self):
        completed = subprocess.CompletedProcess([], 0, '{"released":true}\n', "")
        with mock.patch.object(MCP, "cli_path", return_value="/tmp/sim-manager"), \
             mock.patch.dict(MCP.os.environ, {"SIM_MANAGER_STATE_DIR": "/tmp/shared state"}), \
             mock.patch.object(MCP.subprocess, "run", return_value=completed) as run:
            MCP.run_cli(["run", "ios", "--json", "--", "echo", "ok"])
        argv = run.call_args.args[0]
        self.assertEqual(argv[-3:], ["--", "echo", "ok"])
        self.assertEqual(argv[argv.index("--") - 2:argv.index("--")], ["--state-dir", "/tmp/shared state"])

    def test_json_rpc_lists_tools(self):
        request = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/list"}) + "\n"
        result = subprocess.run([sys.executable, str(SERVER)], input=request, capture_output=True, text=True, check=True)
        response = json.loads(result.stdout)
        self.assertEqual(response["id"], 7)
        self.assertGreaterEqual(len(response["result"]["tools"]), 6)


if __name__ == "__main__":
    unittest.main()
