import signal
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sim_manager import dashboard


class DashboardHandoverTests(unittest.TestCase):
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
