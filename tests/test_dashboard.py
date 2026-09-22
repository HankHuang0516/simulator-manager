import signal
import subprocess
import plistlib
import shutil
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
            icon = root/'assets/SimulatorManager.icns'
            icon.parent.mkdir(parents=True)
            icon.write_bytes((Path(__file__).parents[1]/'assets/SimulatorManager.icns').read_bytes())

            def compile_app(arguments, **_kwargs):
                Path(arguments[arguments.index('-o')+1]).write_bytes(b'app')
                return subprocess.CompletedProcess(arguments,0)

            with patch.object(dashboard.sys,'platform','darwin'), \
                 patch.object(dashboard.subprocess,'run',side_effect=compile_app):
                app = dashboard.build(root=root,state_dir=root/'state')
            with (app/'Contents/Info.plist').open('rb') as file:
                info = plistlib.load(file)
            self.assertFalse(info['LSUIElement'])
            self.assertEqual(info['CFBundleIconFile'],'SimulatorManager.icns')
            self.assertEqual((app/'Contents/Resources/SimulatorManager.icns').read_bytes(),icon.read_bytes())
            self.assertIn('setActivationPolicy(.regular)',source.read_text())
            self.assertIn('Darwin.fcntl(fd,F_SETLK,&request)',source.read_text())
            self.assertIn('dashboard-ui.lock',source.read_text())
            self.assertIn('revealExistingDashboard',source.read_text())
            self.assertIn('DistributedNotificationCenter.default()',source.read_text())

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

    def test_install_stops_exact_build_app_before_copying_dock_app(self):
        with tempfile.TemporaryDirectory(prefix='dashboard install ') as temporary:
            root = Path(temporary)
            source = root/'build/Simulator Manager.app'
            target = root/'Applications/Simulator Manager.app'
            (source/'Contents').mkdir(parents=True)
            (source/'Contents/marker').write_text('managed')

            def copy_app(arguments, **_kwargs):
                if arguments[0] == '/usr/bin/ditto':
                    shutil.copytree(arguments[1],arguments[2])
                elif arguments[0] == '/bin/rm':
                    shutil.rmtree(arguments[-1],ignore_errors=True)
                return subprocess.CompletedProcess(arguments,0)

            with patch.object(dashboard,'stop_existing_dashboard') as stop, \
                 patch.object(dashboard.subprocess,'run',side_effect=copy_app):
                self.assertEqual(dashboard.install_user_app(source,target),target.resolve())
            stop.assert_called_once_with(source.resolve())
            self.assertEqual((target/'Contents/marker').read_text(),'managed')

    def test_open_retries_transient_launchservices_failure(self):
        failure = subprocess.CalledProcessError(1,['open','Simulator Manager.app'])
        success = subprocess.CompletedProcess(['open','Simulator Manager.app'],0)
        with patch.object(dashboard.subprocess,'run',side_effect=[failure,success]) as run, \
             patch.object(dashboard.time,'sleep') as sleep:
            dashboard.open_application(['open','Simulator Manager.app'])
        self.assertEqual(run.call_count,2)
        sleep.assert_called_once_with(.5)

    def test_repeated_onboarding_launch_relies_on_singleton_focus(self):
        app = Path('/tmp/Simulator Manager.app')
        with patch.object(dashboard,'build',return_value=app), \
             patch.object(dashboard,'stop_existing_dashboard') as stop, \
             patch.object(dashboard,'open_application') as opening:
            result = dashboard.launch(state_dir='/tmp/state',onboarding=True,root='/tmp/root')
        stop.assert_not_called()
        self.assertTrue(result['opened'])
        self.assertIn('--onboarding',opening.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
