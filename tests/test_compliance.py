import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sim_manager import compliance
from sim_manager.core import Manager


class ComplianceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='sim compliance ')
        self.state = Path(self.tmp.name)
        config = {
            'version': 1,
            'global_capacity': 1,
            'pools': {
                'ios': {'capacity': 1, 'resources': [{'id':'ios-1','kind':'ios','udid':'11111111-1111-1111-1111-111111111111'}]},
                'android': {'capacity': 1, 'resources': []},
                'gui': {'capacity': 1, 'resources': [{'id':'gui','kind':'generic'}]},
            },
        }
        (self.state/'config.json').write_text(json.dumps(config))
        self.manager = Manager(self.state)
        self.manager.enable('task-1', '/tmp/project', os.getpid())

    def tearDown(self):
        self.manager.close()
        self.tmp.cleanup()

    def test_direct_simctl_in_registered_tree_creates_targeted_guidance(self):
        processes = {
            os.getpid(): (1, 'codex task'),
            43210: (os.getpid(), 'xcrun simctl boot 11111111-1111-1111-1111-111111111111'),
        }
        with patch.object(compliance, '_processes', return_value=processes):
            result = compliance.audit(self.manager, 'task-1')
        self.assertEqual(result['active_findings'], 1)
        finding = result['findings'][0]
        self.assertEqual((finding['session'], finding['platform'], finding['kind']),
                         ('task-1', 'ios', 'direct-simctl'))
        self.assertNotIn('11111111', finding['guidance'])
        self.assertIn('without running simctl shutdown', finding['guidance'])
        self.assertEqual(len(self.manager.status()['compliance']), 1)

    def test_existing_platform_lease_makes_matching_process_compliant(self):
        lease = self.manager.acquire('ios', 'task-1', '/tmp/project', os.getpid(), timeout=0)
        processes = {os.getpid():(1,'codex task'), 43211:(os.getpid(),'xcodebuild test -destination id=ABC')}
        with patch.object(compliance, '_processes', return_value=processes):
            result = compliance.audit(self.manager, 'task-1')
        self.assertEqual(result['active_findings'], 0)
        self.manager.release(lease['token'])

    def test_valid_lease_never_authorizes_runtime_shutdown(self):
        lease = self.manager.acquire('ios', 'task-1', '/tmp/project', os.getpid(), timeout=0)
        processes = {
            os.getpid():(1,'codex task'),
            43213:(os.getpid(),'xcrun simctl shutdown 11111111-1111-1111-1111-111111111111'),
        }
        with patch.object(compliance, '_processes', return_value=processes):
            result = compliance.audit(self.manager, 'task-1')
        self.assertEqual(result['active_findings'], 1)
        finding = result['findings'][0]
        self.assertEqual(finding['kind'], 'unsafe-shutdown')
        self.assertIn('release only', finding['guidance'])
        self.manager.release(lease['token'])

    def test_android_emu_kill_is_shutdown_not_generic_adb(self):
        classified = compliance._classify('adb -s emulator-5554 emu kill')
        self.assertEqual(classified, ('android', 'unsafe-shutdown'))

    def test_disappeared_process_resolves_finding_without_stopping_anything(self):
        first = {os.getpid():(1,'codex task'), 43212:(os.getpid(),'adb -s emulator-5554 shell getprop')}
        with patch.object(compliance, '_processes', return_value=first):
            detected = compliance.audit(self.manager, 'task-1')
        self.assertIn('without closing the emulator', detected['findings'][0]['guidance'])
        with patch.object(compliance, '_processes', return_value={os.getpid():(1,'codex task')}):
            result = compliance.audit(self.manager, 'task-1')
        self.assertEqual(result['active_findings'], 0)
        self.assertEqual(result['findings'][0]['active'], 0)
        self.assertEqual(self.manager.status()['compliance'], [])

    def test_unobservable_registration_is_reported_as_coverage_gap(self):
        with self.manager.transaction():
            self.manager.db.execute(
                "UPDATE sessions SET owner_pid=?,owner_start=? WHERE session=?",
                (999999, 'missing-process', 'task-1'))
        with patch.object(compliance, '_processes', return_value={}):
            result = compliance.audit(self.manager)
        self.assertEqual(result['registered_sessions'], 1)
        self.assertEqual(result['observable_sessions'], 0)
        self.assertEqual(result['active_findings'], 0)

    def test_shared_owner_is_a_coverage_gap_even_when_one_label_has_a_lease(self):
        self.manager.enable('task-2', '/tmp/other-project', os.getpid())
        lease = self.manager.acquire('ios', 'task-2', '/tmp/other-project', os.getpid(), timeout=0)
        processes = {os.getpid(): (1, 'codex host'),
                     43220: (os.getpid(), 'xcodebuild test -destination id=ABC')}
        try:
            with patch.object(compliance, '_processes', return_value=processes):
                result = compliance.audit(self.manager)
                targeted = compliance.audit(self.manager, 'task-1')
            self.assertEqual(result['registered_sessions'], 2)
            self.assertEqual(result['observable_sessions'], 2)
            self.assertEqual(result['ambiguous_sessions'], 2)
            self.assertEqual(result['active_findings'], 0)
            self.assertEqual(targeted['ambiguous_sessions'], 1)
            self.assertEqual(targeted['active_findings'], 0)
            self.assertEqual(self.manager.status()['compliance'], [])
        finally:
            self.manager.release(lease['token'])

    def test_targeted_audit_does_not_clear_other_tasks_finding(self):
        self.manager.enable('task-2', '/tmp/other-project', os.getpid())
        with self.manager.transaction():
            self.manager.db.execute(
                "UPDATE sessions SET owner_pid=?,owner_start=? WHERE session=?",
                (999999, 'synthetic-process', 'task-2'))
        both = {os.getpid(): (1, 'task-1'), 43221: (os.getpid(), 'xcrun simctl boot ABC'),
                999999: (1, 'task-2'), 43222: (999999, 'adb -s emulator-5554 shell getprop')}
        with patch.object(compliance, 'process_alive', return_value=True), \
             patch.object(compliance, '_processes', return_value=both):
            first = compliance.audit(self.manager)
            self.assertEqual(first['active_findings'], 2)
            second = compliance.audit(self.manager, 'task-1')
        self.assertEqual(second['active_findings'], 1)
        self.assertEqual(len(self.manager.status()['compliance']), 2)


if __name__ == '__main__':
    unittest.main()
