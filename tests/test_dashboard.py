import signal
import subprocess
import plistlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sim_manager import dashboard


class DashboardHandoverTests(unittest.TestCase):
    def test_bundle_and_runtime_policy_keep_dashboard_in_dock(self):
        with tempfile.TemporaryDirectory(prefix='dashboard dock ') as temporary:
            root = Path(temporary)
            source = root/'dashboard/SimulatorManager.swift'
            source.parent.mkdir(parents=True)
            source.write_text((Path(__file__).parents[1]/'dashboard/SimulatorManager.swift').read_text())

            def compile_app(arguments, **_kwargs):
                Path(arguments[arguments.index('-o')+1]).write_bytes(b'app')
                return subprocess.CompletedProcess(arguments,0)

            with patch.object(dashboard.sys,'platform','darwin'), \
                 patch.object(dashboard.subprocess,'run',side_effect=compile_app):
                app = dashboard.build(root=root,state_dir=root/'state')
            with (app/'Contents/Info.plist').open('rb') as file:
                self.assertFalse(plistlib.load(file)['LSUIElement'])
            self.assertIn('setActivationPolicy(.regular)',source.read_text())

    def test_process_inventory_matches_only_exact_managed_executable_path(self):
        with tempfile.TemporaryDirectory(prefix='dashboard app ') as temporary:
            app = Path(temporary)/'Simulator Manager.app'
            executable = (app/'Contents/MacOS/SimulatorManager').resolve()
            listing = '\n'.join((
                f' 101 {executable} --state-dir /tmp/shared',
                f' 102 {executable}.old --state-dir /tmp/shared',
                ' 103 /tmp/SimulatorManager --state-dir /tmp/shared',
            ))
            completed = subprocess.CompletedProcess([],0,listing,'')
            with patch.object(dashboard.subprocess,'run',return_value=completed):
                self.assertEqual(dashboard.running_dashboard_pids(app),[101])

    def test_handover_sends_only_graceful_term_and_waits_for_exit(self):
        calls = []

        def kill(pid, sig):
            calls.append((pid,sig))
            if sig == 0:
                raise ProcessLookupError

        with patch.object(dashboard,'running_dashboard_pids',return_value=[201,202]), \
             patch.object(dashboard.os,'kill',side_effect=kill):
            dashboard.stop_existing_dashboard('/tmp/Simulator Manager.app')
        self.assertEqual([call for call in calls if call[1] == signal.SIGTERM],
                         [(201,signal.SIGTERM),(202,signal.SIGTERM)])
        self.assertFalse(any(sig == signal.SIGKILL for _pid,sig in calls))


if __name__ == '__main__':
    unittest.main()
