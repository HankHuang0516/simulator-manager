import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from sim_manager.core import Manager
from sim_manager.provision import prepare


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='sim activation ')
        self.base=Path(self.tmp.name).resolve()
        self.state=self.base/'state'
        self.state.mkdir()
        self.config={'version':1,'global_capacity':1,'pools':{'ios':{'capacity':1,'resources':[]},
                     'android':{'capacity':1,'resources':[]},'gui':{'capacity':1,'resources':[{'id':'gui','kind':'generic'}]}}}
        self.write_config()

    def tearDown(self):
        self.tmp.cleanup()

    def write_config(self):
        (self.state/'config.json').write_text(json.dumps(self.config))

    def test_enable_records_intent_without_reserving_device(self):
        m=Manager(self.state)
        first=m.enable('session-a','project-a')
        second=m.enable('session-b','project-b')
        m.enable('session-a','project-a')
        self.assertTrue(first['shared_mode'])
        self.assertEqual(first['state_dir'],second['state_dir'])
        status=m.status()
        self.assertEqual(len(status['sessions']),2)
        self.assertEqual(status['leases'],[])
        self.assertEqual(status['queue'],[])
        m.close()

    def test_generated_session_is_returned(self):
        with patch.dict(os.environ,{},clear=True):
            m=Manager(self.state)
            result=m.enable()
            self.assertTrue(result['session'].startswith('session-'))
            m.close()

    def test_prepare_uses_dedicated_creator_once(self):
        m=Manager(self.state)
        dedicated={'id':'owned-ios','kind':'ios','udid':'11111111-1111-1111-1111-111111111111',
                   'cost':1,'enabled':True,'allow_attach':False}
        with patch('sim_manager.provision.ios_resource',return_value=dedicated) as creator:
            self.assertTrue(prepare(m,('ios',))['ios']['created'])
            self.assertFalse(prepare(m,('ios',))['ios']['created'])
            self.assertEqual(creator.call_count,1)
        self.assertEqual(json.loads((self.state/'config.json').read_text())['pools']['ios']['resources'][0]['udid'],dedicated['udid'])
        m.close()

    def test_prepare_preserves_disabled_pool(self):
        self.config['pools']['ios']['capacity']=0
        self.write_config()
        m=Manager(self.state)
        with patch('sim_manager.provision.ios_resource') as creator:
            result=prepare(m,('ios',))
            self.assertFalse(result['ios']['ready'])
            creator.assert_not_called()
        m.close()

    def test_prepare_defers_when_another_session_is_working(self):
        m=Manager(self.state)
        lease=m.acquire('gui',owner_pid=os.getpid(),timeout=0)
        with patch('sim_manager.provision.ios_resource') as creator:
            result=prepare(m,('ios',))
            self.assertFalse(result['ios']['ready'])
            creator.assert_not_called()
        m.release(lease['token']);m.close()

    def test_missing_sdk_still_enables_coordination(self):
        from sim_manager.core import ManagerError
        m=Manager(self.state)
        enabled=m.enable('session-without-sdk')
        with patch('sim_manager.provision.ios_resource',side_effect=ManagerError('No installed runtime')):
            readiness=prepare(m,('ios',))
        self.assertTrue(enabled['shared_mode'])
        self.assertFalse(readiness['ios']['ready'])
        self.assertEqual(readiness['ios']['reason'],'No installed runtime')
        m.close()

    def test_concurrent_bootstrap_reuses_install_and_state(self):
        args=[str(ROOT/'bootstrap.sh'),'--prefix',str(self.base/'installed'),'--bin-dir',str(self.base/'bin'),
              '--skill-dir',str(self.base/'skills'),'--state-dir',str(self.state),'--no-prepare']
        processes=[subprocess.Popen([*args,'--session',s],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                   for s in ('session-a','session-b')]
        try:
            results=[]
            for p in processes:
                out,err=p.communicate(timeout=15)
                self.assertEqual(p.returncode,0,out+err)
                results.append(json.loads(out))
        finally:
            for p in processes:
                if p.poll() is None:p.kill();p.communicate()
        self.assertEqual(sum(r['installed'] for r in results),1)
        self.assertEqual(results[0]['state_dir'],results[1]['state_dir'])
        m=Manager(self.state)
        self.assertEqual({s['session'] for s in m.status()['sessions']},{'session-a','session-b'})
        self.assertEqual(m.status()['leases'],[])
        m.close()

    def test_bootstrap_preserves_existing_pool_config(self):
        args=[str(ROOT/'bootstrap.sh'),'--prefix',str(self.base/'installed'),'--bin-dir',str(self.base/'bin'),
              '--skill-dir',str(self.base/'skills'),'--state-dir',str(self.state),'--no-prepare','--session','same-session']
        before=(self.state/'config.json').read_bytes()
        for _ in range(2):
            p=subprocess.run(args,capture_output=True,text=True,timeout=15)
            self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertEqual(before,(self.state/'config.json').read_bytes())

    def test_ios_creation_selects_runtime_and_never_boots(self):
        from sim_manager.provision import ios_resource
        inventory={'runtimes':[{'isAvailable':True,'identifier':'com.apple.CoreSimulator.SimRuntime.iOS-26-0','version':'26.0',
                               'supportedDeviceTypes':[{'name':'iPhone 17','identifier':'phone-id'}]}]}
        m=Manager(self.state)
        with patch('sim_manager.provision.tool',return_value='fake-xcrun'),patch('sim_manager.provision.call',side_effect=[json.dumps(inventory),'11111111-1111-1111-1111-111111111111']) as call:
            resource=ios_resource(m)
            command=call.call_args_list[-1].args[0]
            self.assertEqual(command[:3],['fake-xcrun','simctl','create'])
            self.assertNotIn('boot',command)
            self.assertEqual(resource['kind'],'ios')
        m.close()

    def test_android_creation_isolated_avd_home_and_existing_image(self):
        from sim_manager.provision import android_resource
        sdk=self.base/'sdk'
        image=sdk/'system-images/android-36/google_apis/arm64-v8a'
        image.mkdir(parents=True)
        (image/'system.img').touch()
        (image/'package.xml').write_text('<repository><localPackage path="system-images;android-36;google_apis;arm64-v8a"/></repository>')
        self.config['android_sdk']=str(sdk)
        self.write_config()
        m=Manager(self.state)
        with patch('sim_manager.provision.tool',return_value='fake-tool'),patch('sim_manager.provision.platform.machine',return_value='arm64'),patch('sim_manager.provision.ports_free',return_value=True),patch('sim_manager.provision.subprocess.run',return_value=subprocess.CompletedProcess([],0,'','')) as run:
            resource=android_resource(m)
            argv=run.call_args.args[0]
            self.assertEqual(argv[:3],['fake-tool','create','avd'])
            self.assertNotIn('--force',argv)
            self.assertNotIn('-f',argv)
            self.assertEqual(run.call_args.kwargs['env']['ANDROID_AVD_HOME'],str(self.state/'avds'))
            self.assertEqual(resource['avd_home'],str(self.state/'avds'))
        m.close()


if __name__=='__main__':unittest.main()
