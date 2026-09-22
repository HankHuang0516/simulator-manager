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
        self.assertEqual(len(self.manager.status()['compliance']), 1)

    def test_existing_platform_lease_makes_matching_process_compliant(self):
        lease = self.manager.acquire('ios', 'task-1', '/tmp/project', os.getpid(), timeout=0)
        processes = {os.getpid():(1,'codex task'), 43211:(os.getpid(),'xcodebuild test -destination id=ABC')}
        with patch.object(compliance, '_processes', return_value=processes):
            result = compliance.audit(self.manager, 'task-1')
        self.assertEqual(result['active_findings'], 0)
        self.manager.release(lease['token'])

    def test_disappeared_process_resolves_finding_without_stopping_anything(self):
        first = {os.getpid():(1,'codex task'), 43212:(os.getpid(),'adb -s emulator-5554 shell getprop')}
        with patch.object(compliance, '_processes', return_value=first):
            compliance.audit(self.manager, 'task-1')
        with patch.object(compliance, '_processes', return_value={os.getpid():(1,'codex task')}):
            result = compliance.audit(self.manager, 'task-1')
        self.assertEqual(result['active_findings'], 0)
        self.assertEqual(result['findings'][0]['active'], 0)
        self.assertEqual(self.manager.status()['compliance'], [])


if __name__ == '__main__':
    unittest.main()
