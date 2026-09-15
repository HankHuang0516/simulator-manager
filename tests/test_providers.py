import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from sim_manager.core import Manager
from sim_manager import providers
from sim_manager.core import ManagerError
from unittest.mock import patch

FAKE = r'''
import json,os,sys,time
from pathlib import Path
store=Path(os.environ['FAKE_STORE'])
state=json.loads(store.read_text())
name=Path(sys.argv[0]).name
args=sys.argv[1:]
with (store.parent/'calls').open('a') as f:f.write(json.dumps([name,args])+'\n')
def save():store.write_text(json.dumps(state))
if name=='fake-xcrun':
    if args[:3]==['simctl','list','devices']:
        print(json.dumps({'devices':{'test-runtime':[{'udid':'DEDICATED-UDID','state':state['ios_state'],'isAvailable':True}]}}))
    elif args[:2]==['simctl','boot']:
        state['ios_state']='Booted';save()
    elif args[:2]==['simctl','bootstatus']:
        if state.get('boot_fail'):sys.exit(8)
        if state.get('boot_slow'):time.sleep(5)
    else:sys.exit(99)
elif name=='fake-emulator':
    if args==['-list-avds']:print('DedicatedAVD')
    else:
        assert args==['-avd','DedicatedAVD','-port','5680','-no-snapshot-save']
        if state.get('emulator_fail'):sys.exit(9)
        state['devices']={'emulator-5680':'device'};save();time.sleep(15)
elif name=='fake-adb':
    if args==['devices']:
        print('List of devices attached')
        for serial,value in state['devices'].items():print(serial,value,sep='\t')
    elif args[0]=='-s':
        assert args[1].startswith('emulator-')
        if args[2:]==['emu','avd','name']:
            failures=state.get('name_failures',0)
            if failures:
                state['name_failures']=failures-1
                if state.get('drop_failed_serial'):state['devices'].pop(args[1],None)
                save();print('TCP connection refused',file=sys.stderr);sys.exit(1)
            if state.get('name_always_fails'):
                print('TCP connection refused',file=sys.stderr);sys.exit(1)
            print(state.get('avd','DedicatedAVD')+'\nOK')
        elif args[2:]==['shell','getprop','sys.boot_completed']:
            failures=state.get('getprop_failures',0)
            if failures:
                state['getprop_failures']=failures-1;save();sys.exit(1)
            print('1')
        else:sys.exit(99)
    else:sys.exit(99)
'''


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='sim-provider ')
        self.state=Path(self.tmp.name)
        self.store=self.state/'fake-state.json'
        self.store.write_text(json.dumps({'ios_state':'Shutdown','devices':{}}))
        self.old_env=os.environ.get('FAKE_STORE')
        os.environ['FAKE_STORE']=str(self.store)
        tools={}
        for name in ('xcrun','adb','emulator'):
            path=self.state/('fake-'+name)
            path.write_text('#!'+sys.executable+'\n'+FAKE);path.chmod(0o755)
            tools[name]=str(path)
        self.config={'version':1,'monitor':{'low_disk_gib':.000001,'critical_disk_gib':.0000001},'tools':tools,'global_capacity':1,'poll_seconds':.025,
                     'pools':{'ios':{'capacity':1,'resources':[{'id':'ios-test','kind':'ios','udid':'DEDICATED-UDID'}]},
                              'android':{'capacity':1,'resources':[{'id':'android-test','kind':'android','avd':'DedicatedAVD','port':5680}]}}}
        (self.state/'config.json').write_text(json.dumps(self.config))

    def tearDown(self):
        m=Manager(self.state)
        for runtime in m.db.execute('SELECT * FROM runtimes'):
            if runtime['pid']:
                try:os.killpg(runtime['pid'],signal.SIGKILL)
                except ProcessLookupError:pass
        m.close()
        if self.old_env is None:os.environ.pop('FAKE_STORE',None)
        else:os.environ['FAKE_STORE']=self.old_env
        self.tmp.cleanup()

    def update(self,**values):
        state=json.loads(self.store.read_text());state.update(values)
        self.store.write_text(json.dumps(state))

    def cli(self,*args):
        return subprocess.run([sys.executable,str(ROOT/'bin/sim-manager'),'--state-dir',str(self.state),*map(str,args)],
                              capture_output=True,text=True,timeout=10)

    def status(self):
        return json.loads(self.cli('status','--json').stdout)

    def calls(self):
        path=self.state/'calls'
        return [json.loads(v) for v in path.read_text().splitlines()] if path.exists() else []

    def test_ios_boot_and_reuse(self):
        for _ in range(2):
            p=self.cli('run','ios','--boot','--json','--',sys.executable,'-c','import os; assert os.environ["SIM_MANAGER_UDID"]=="DEDICATED-UDID"')
            self.assertEqual(p.returncode,0,p.stdout+p.stderr)
            self.assertEqual(self.status()['leases'],[])
        boots=[c for c in self.calls() if c[1][:2]==['simctl','boot']]
        self.assertEqual(len(boots),1)
        self.assertEqual(boots[0][1],['simctl','boot','DEDICATED-UDID'])

    def test_ios_external_booted_refused(self):
        self.update(ios_state='Booted')
        p=self.cli('run','ios','--boot','--json','--',sys.executable,'-c','raise SystemExit(99)')
        self.assertNotEqual(p.returncode,0)
        self.assertIn('outside this manager',p.stderr)
        self.assertEqual(self.status()['leases'],[])
        self.assertFalse(any(c[1][:2]==['simctl','bootstatus'] for c in self.calls()))

    def test_ios_boot_failure_manual_releases(self):
        self.update(boot_fail=True)
        p=self.cli('acquire','ios','--owner-pid',os.getpid(),'--json')
        token=json.loads(p.stdout)['token']
        p=self.cli('boot',token,'--timeout',2,'--json')
        self.assertNotEqual(p.returncode,0)
        self.assertFalse(json.loads(p.stdout)['booted'])
        self.assertEqual(self.status()['leases'],[])

    def test_ios_boot_timeout_releases(self):
        self.update(boot_slow=True)
        p=self.cli('run','ios','--boot','--boot-timeout',.2,'--json','--',sys.executable,'-c','raise SystemExit(99)')
        self.assertNotEqual(p.returncode,0)
        self.assertEqual(self.status()['leases'],[])

    def test_android_start_explicit_avd_port_and_reuse(self):
        for _ in range(2):
            p=self.cli('run','android','--boot','--boot-timeout',4,'--json','--',sys.executable,'-c','import os; assert os.environ["SIM_MANAGER_SERIAL"]=="emulator-5680"')
            self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        launches=[c for c in self.calls() if c[0]=='fake-emulator' and c[1]!=['-list-avds']]
        self.assertEqual(len(launches),1)
        self.assertEqual(self.status()['leases'],[])

    def test_android_launch_failure_releases(self):
        self.update(emulator_fail=True)
        p=self.cli('run','android','--boot','--boot-timeout',2,'--json','--',sys.executable,'-c','raise SystemExit(99)')
        self.assertNotEqual(p.returncode,0)
        self.assertEqual(self.status()['leases'],[])

    def test_android_stale_assigned_transport_disappears_before_cold_boot(self):
        self.update(devices={'emulator-5680':'device'},name_failures=1,drop_failed_serial=True)
        p=self.cli('run','android','--boot','--boot-timeout',4,'--json','--',sys.executable,'-c','pass')
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertEqual(len([c for c in self.calls() if c[0]=='fake-emulator' and c[1]!=['-list-avds']]),1)
        self.assertEqual(self.status()['leases'],[])

    def test_android_warm_name_and_boot_property_recover_without_relaunch(self):
        p=self.cli('run','android','--boot','--boot-timeout',4,'--json','--',sys.executable,'-c','pass')
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.update(name_failures=1,getprop_failures=1)
        p=self.cli('run','android','--boot','--boot-timeout',4,'--json','--',sys.executable,'-c','pass')
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertEqual(len([c for c in self.calls() if c[0]=='fake-emulator' and c[1]!=['-list-avds']]),1)
        self.assertEqual(self.status()['leases'],[])

    def test_android_unknown_other_transport_must_disappear_before_launch(self):
        self.update(devices={'emulator-5678':'offline'},name_failures=1,drop_failed_serial=True)
        p=self.cli('run','android','--boot','--boot-timeout',4,'--json','--',sys.executable,'-c','pass')
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertEqual(self.status()['leases'],[])

    def test_android_persistent_unknown_transport_blocks_child_and_launch(self):
        self.update(devices={'emulator-5678':'device'},name_always_fails=True)
        p=self.cli('run','android','--boot','--boot-timeout',.4,'--json','--',sys.executable,'-c','raise SystemExit(99)')
        self.assertEqual(p.returncode,1,p.stdout+p.stderr)
        self.assertIn('fully identifiable',p.stderr)
        self.assertTrue(json.loads(p.stdout)['released'])
        self.assertFalse(any(c[0]=='fake-emulator' for c in self.calls()))
        self.assertEqual(self.status()['leases'],[])

    def test_android_waits_for_exiting_ports_before_launch(self):
        m=Manager(self.state)
        lease=m.acquire('android',owner_pid=os.getpid(),timeout=0)
        children=[]
        spawn=providers.spawn_gated
        def track(*args,**kwargs):
            child,gate=spawn(*args,**kwargs);children.append(child);return child,gate
        try:
            with patch.object(providers,'ports_free',side_effect=[False,True,True]) as free, \
                 patch.object(providers,'spawn_gated',side_effect=track):
                providers.boot(m,lease['resource'],timeout=4)
            self.assertEqual(free.call_count,3)
            self.assertEqual(len([c for c in self.calls() if c[0]=='fake-emulator' and c[1]!=['-list-avds']]),1)
        finally:
            m.release(lease['token']);m.close()
            for child in children:
                if child.poll() is None:os.killpg(child.pid,signal.SIGTERM)
                child.wait(timeout=5)

    def test_android_persistently_occupied_ports_never_launch(self):
        m=Manager(self.state)
        lease=m.acquire('android',owner_pid=os.getpid(),timeout=0)
        try:
            with patch.object(providers,'ports_free',return_value=False):
                with self.assertRaises(ManagerError):
                    providers.boot(m,lease['resource'],timeout=.3)
            self.assertFalse(any(c[0]=='fake-emulator' for c in self.calls()))
        finally:
            m.release(lease['token']);m.close()

    def test_android_other_avd_at_port_refused(self):
        self.update(devices={'emulator-5680':'device'},avd='OtherAVD')
        p=self.cli('run','android','--boot','--json','--',sys.executable,'-c','raise SystemExit(99)')
        self.assertNotEqual(p.returncode,0)
        self.assertIn('another AVD',p.stderr)
        self.assertEqual(self.status()['leases'],[])

    def test_android_same_avd_at_different_port_refused(self):
        self.update(devices={'emulator-5678':'device'})
        p=self.cli('run','android','--boot','--json','--',sys.executable,'-c','raise SystemExit(99)')
        self.assertNotEqual(p.returncode,0)
        self.assertIn('duplicate launch',p.stderr)
        self.assertEqual(self.status()['leases'],[])

    def test_android_external_same_avd_refused(self):
        self.update(devices={'emulator-5680':'device'})
        p=self.cli('run','android','--boot','--json','--',sys.executable,'-c','raise SystemExit(99)')
        self.assertNotEqual(p.returncode,0)
        self.assertIn('outside manager',p.stderr)
        self.assertEqual(self.status()['leases'],[])

    def test_invalid_android_target_fails_before_launch_and_releases(self):
        home=self.state/'avds';home.mkdir();(home/'DedicatedAVD.ini').write_text('target=android-0\n')
        self.config['pools']['android']['resources'][0]['avd_home']=str(home)
        (self.state/'config.json').write_text(json.dumps(self.config))
        p=self.cli('run','android','--boot','--json','--',sys.executable,'-c','raise SystemExit(99)')
        self.assertEqual(p.returncode,1,p.stdout+p.stderr);self.assertTrue(json.loads(p.stdout)['released'])
        self.assertIn('target is invalid',p.stderr)
        self.assertFalse(any(c[0]=='fake-emulator' and c[1]!=['-list-avds'] for c in self.calls()))
        self.assertEqual(self.status()['leases'],[])

    def test_low_disk_defers_new_boot_and_releases_without_launch(self):
        self.config['monitor']['low_disk_gib']=1000000
        (self.state/'config.json').write_text(json.dumps(self.config))
        for pool in ('ios','android'):
            p=self.cli('run',pool,'--boot','--json','--',sys.executable,'-c','raise SystemExit(99)')
            self.assertEqual(p.returncode,1,p.stdout+p.stderr)
            self.assertTrue(json.loads(p.stdout)['released'])
            self.assertIn('boot deferred',p.stderr)
        self.assertEqual(self.status()['leases'],[])
        self.assertFalse(any(c[1][:2]==['simctl','boot'] or (c[0]=='fake-emulator' and c[1]!=['-list-avds']) for c in self.calls()))

    def test_read_only_discovery_and_no_shutdown_commands(self):
        self.assertEqual(self.cli('discover','ios','--json').returncode,0)
        self.assertEqual(self.cli('discover','android','--json').returncode,0)
        forbidden={'shutdown','erase','kill-server','kill'}
        self.assertFalse(any(forbidden.intersection(c[1]) for c in self.calls()))


class AndroidInventoryTests(unittest.TestCase):
    def test_changed_inventory_rechecks_every_emulator_identity(self):
        first={'emulator-5680':'device'}
        second={**first,'emulator-5678':'device'}
        with patch.object(providers,'android_devices',side_effect=[first,second,second,second]), \
             patch.object(providers,'avd_name',side_effect=['DedicatedAVD','DedicatedAVD','OtherAVD']) as name, \
             patch.object(providers.time,'sleep'):
            devices,names=providers.android_inventory('adb',providers.time.monotonic()+2)
        self.assertEqual(devices,second)
        self.assertEqual(names,{'emulator-5680':'DedicatedAVD','emulator-5678':'OtherAVD'})
        self.assertEqual([c.args[1] for c in name.call_args_list],['emulator-5680','emulator-5680','emulator-5678'])

    def test_unidentified_inventory_uses_original_deadline_for_every_command(self):
        clock=[0.0];timeouts=[]
        def devices(adb,timeout):
            timeouts.append((clock[0],timeout));return {'emulator-5678':'offline'}
        def name(adb,serial,timeout):
            timeouts.append((clock[0],timeout));raise ManagerError('connection refused')
        def sleep(seconds):clock[0]+=seconds
        with patch.object(providers.time,'monotonic',side_effect=lambda:clock[0]), \
             patch.object(providers.time,'sleep',side_effect=sleep), \
             patch.object(providers,'android_devices',side_effect=devices), \
             patch.object(providers,'avd_name',side_effect=name):
            with self.assertRaisesRegex(ManagerError,'fully identifiable'):
                providers.android_inventory('adb',.7)
        self.assertEqual(clock[0],.7)
        self.assertTrue(all(0<timeout<=.7-start for start,timeout in timeouts))


class InstallTests(unittest.TestCase):
    def test_isolated_install_and_preservation(self):
        with tempfile.TemporaryDirectory(prefix='sim install ') as tmp:
            base=Path(tmp)
            args=[str(ROOT/'install.sh'),'--prefix',str(base/'install'),
                  '--bin-dir',str(base/'tools'),'--skill-dir',str(base/'skills'),'--state-dir',str(base/'state')]
            p=subprocess.run(args,capture_output=True,text=True,timeout=10)
            self.assertEqual(p.returncode,0,p.stdout+p.stderr)
            executable=base/'tools/sim-manager'
            p=subprocess.run([str(executable),'status','--state-dir',str(base/'state'),'--json'],capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stdout+p.stderr)
            config=base/'state/config.json'
            c=json.loads(config.read_text());c['global_capacity']=1
            config.write_text(json.dumps(c))
            before=config.read_bytes()
            p=subprocess.run([*args,'--upgrade'],capture_output=True,text=True,timeout=10)
            self.assertEqual(p.returncode,0,p.stdout+p.stderr)
            self.assertEqual(config.read_bytes(),before)
            self.assertTrue((base/'skills/simulator-manager/SKILL.md').is_file())
            p=subprocess.run(args,capture_output=True,text=True)
            self.assertNotEqual(p.returncode,0)


if __name__=='__main__':unittest.main()
