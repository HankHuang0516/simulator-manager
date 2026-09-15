import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from sim_manager.core import Manager, ManagerError, OwnershipError, WaitTimeout, load_config, process_stamp
from sim_manager.monitor import advance, update, admission
from sim_manager.dynamic import retire_idle
from sim_manager.watchdog import ensure, stop, tick

FAKE = r'''
import json,os,signal,sqlite3,sys,time,uuid
from pathlib import Path
args=sys.argv[1:];name=Path(sys.argv[0]).name
con=sqlite3.connect(os.environ['DYNAMIC_FAKE_DB'],timeout=10)
con.execute('CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY,kind TEXT,state TEXT,name TEXT,pid INTEGER)')
con.execute('CREATE TABLE IF NOT EXISTS calls(name TEXT,args TEXT)')
con.execute('INSERT INTO calls VALUES(?,?)',(name,json.dumps(args)));con.commit()
if name=='xcrun':
 if args[:2]==['simctl','list']:
  print(json.dumps({'devices':{'runtime':[{'udid':r[0],'state':r[2],'isAvailable':True,'name':r[3]} for r in con.execute("SELECT * FROM devices WHERE kind='ios'")]},
   'runtimes':[{'identifier':'com.apple.CoreSimulator.SimRuntime.iOS-20-0','version':'20.0','isAvailable':True}],
   'devicetypes':[{'name':'iPhone 20','identifier':'iphone20'}]}))
 elif args[:2]==['simctl','create']:
  time.sleep(float(os.environ.get('DYNAMIC_CREATE_DELAY','0')))
  udid=str(uuid.uuid4());con.execute('INSERT INTO devices VALUES(?,?,?,?,?)',(udid,'ios','Shutdown',args[2],None));con.commit();print(udid)
 elif args[:2]==['simctl','boot']:
  con.execute("UPDATE devices SET state='Booted' WHERE id=?",(args[2],));con.commit()
 elif args[:2]==['simctl','bootstatus']:time.sleep(float(os.environ.get('DYNAMIC_BOOT_DELAY','0')))
 elif args[:2]==['simctl','shutdown']:
  assert args[2]!='all';con.execute("UPDATE devices SET state='Shutdown' WHERE id=?",(args[2],));con.commit()
 else:sys.exit(99)
elif name=='avdmanager':
 assert args[:2]==['create','avd']
 avd=args[args.index('-n')+1];home=Path(os.environ['ANDROID_AVD_HOME'])
 (home/(avd+'.ini')).write_text('path='+args[args.index('-p')+1]+'\n')
 content=Path(args[args.index('-p')+1]);content.mkdir()
 (content/'config.ini').write_text('image.sysdir.1 = '+args[args.index('-k')+1].replace(';','/')+'/\n')
elif name=='emulator':
 if args==['-list-avds']:print('\n'.join(p.stem for p in Path(os.environ['ANDROID_AVD_HOME']).glob('*.ini')))
 else:
  avd=args[args.index('-avd')+1];serial='emulator-'+args[args.index('-port')+1]
  con.execute('INSERT OR REPLACE INTO devices VALUES(?,?,?,?,?)',(serial,'android','device',avd,os.getpid()));con.commit()
  time.sleep(60)
elif name=='adb':
 if args==['devices']:
  print('List of devices attached')
  for r in con.execute("SELECT * FROM devices WHERE kind='android'"):print(r[0]+'\t'+r[2])
 elif args[:1]==['-s']:
  r=con.execute('SELECT * FROM devices WHERE id=?',(args[1],)).fetchone()
  if args[2:]==['emu','avd','name']:print(r[3]+'\nOK')
  elif args[2:]==['shell','getprop','sys.boot_completed']:print('1')
  elif args[2:]==['emu','kill']:
   assert r;os.kill(r[4],signal.SIGTERM);con.execute('DELETE FROM devices WHERE id=?',(r[0],));con.commit();print('OK')
  else:sys.exit(99)
 else:sys.exit(99)
'''

class DynamicTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='dynamic pool ')
        self.state=Path(self.tmp.name)
        self.saved_env=dict(os.environ)
        os.environ.update(DYNAMIC_FAKE_DB=str(self.state/'sdk.sqlite'),PYTHONPATH=str(ROOT))
        tools={}
        for name in ('xcrun','adb','emulator','avdmanager'):
            path=self.state/name;path.write_text('#!'+sys.executable+'\n'+FAKE);path.chmod(0o755);tools[name]=str(path)
        sdk=self.state/'sdk';image=sdk/'system-images/android-40/google_apis'/('arm64-v8a' if __import__('platform').machine().lower() in ('arm64','aarch64') else 'x86_64')
        image.mkdir(parents=True);(image/'system.img').touch();(image/'source.properties').write_text('AndroidVersion.ApiLevel=40\n')
        (image/'package.xml').write_text('<sdk><localPackage path="system-images;android-40;google_apis;'+image.name+'"/></sdk>')
        self.config={'version':1,'mode':'dynamic','global_capacity':1,'lease_seconds':10,'poll_seconds':.02,
                     'tools':tools,'android_sdk':str(sdk),'policy':{'max_hold_seconds':10,'waiter_slice_seconds':10,'yield_grace_seconds':.5},
                     'dynamic':{'max_parallel':3,'idle_shutdown_seconds':60},
                     'monitor':{'sample_seconds':.05,'daemon':False,'high_load_ratio':100,'critical_load_ratio':200,'low_memory_percent':.01,'critical_memory_percent':.001,'low_disk_gib':.001,'critical_disk_gib':.0001},
                     'pools':{'ios':{'capacity':1,'resources':[]},'android':{'capacity':1,'resources':[]},'gui':{'capacity':1,'resources':[{'id':'gui','kind':'generic'}]}}}
        self.write();self.children=[]

    def write(self):
        (self.state/'config.json').write_text(json.dumps(self.config))

    def tearDown(self):
        m=Manager(self.state,allow_saved_config=True)
        try:
            stop(m)
            for r in m.db.execute('SELECT * FROM runtimes'):
                if r['pid']:
                    try:os.killpg(r['pid'],signal.SIGKILL)
                    except ProcessLookupError:pass
        finally:m.close()
        for child in self.children:
            if child.poll() is None:child.kill()
            child.communicate(timeout=5)
        os.environ.clear();os.environ.update(self.saved_env)
        self.tmp.cleanup()

    def cli(self,*args,**kw):
        return subprocess.run([sys.executable,str(ROOT/'bin/sim-manager'),'--state-dir',str(self.state),*map(str,args)],capture_output=True,text=True,timeout=kw.pop('timeout',15),**kw)

    def launch(self,*args):
        child=subprocess.Popen([sys.executable,str(ROOT/'bin/sim-manager'),'--state-dir',str(self.state),*map(str,args)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.children.append(child);return child

    def until(self,predicate,timeout=6):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            value=predicate()
            if value:return value
            time.sleep(.025)
        self.fail('condition did not become true')

    def status(self):
        m=Manager(self.state)
        try:return m.status()
        finally:m.close()

    def lease(self,session='a',pool='ios',**kwargs):
        m=Manager(self.state)
        try:return m.acquire(pool,session=session,owner_pid=os.getpid(),timeout=0,**kwargs)
        finally:m.close()

    def release(self,token):
        m=Manager(self.state)
        try:return m.release(token)
        finally:m.close()

    def test_session_environment_stability_and_project_isolation(self):
        a=self.lease();self.release(a['token']);again=self.lease()
        self.assertEqual(a['resource']['udid'],again['resource']['udid']);self.release(again['token'])
        b=self.lease('b');c=self.lease('a',project=str(self.state/'other-project'))
        self.assertEqual(len({a['resource']['udid'],b['resource']['udid'],c['resource']['udid']}),3)
        self.release(b['token']);self.release(c['token'])

    def test_parallel_creation_reserves_capacity_before_sdk_call(self):
        os.environ['DYNAMIC_CREATE_DELAY']='.3'
        ps=[self.launch('acquire','ios','--session',str(i),'--owner-pid',os.getpid(),'--json') for i in range(3)]
        self.until(lambda:len(self.status()['leases'])==3)
        self.assertEqual(self.cli('acquire','ios','--session','fourth','--timeout',0).returncode,3)
        leases=[]
        for p in ps:
            out,err=p.communicate(timeout=10);self.assertEqual(p.returncode,0,out+err);leases.append(json.loads(out))
        self.assertEqual(len({r['resource_id'] for r in leases}),3)
        for r in leases:self.release(r['token'])

    def test_android_private_writable_home_unique_avd_and_port(self):
        a=self.lease('a','android');b=self.lease('b','android')
        for key in ('avd','avd_home','port'):self.assertNotEqual(a['resource'][key],b['resource'][key])
        self.assertTrue(Path(a['resource']['avd_home']).is_relative_to(self.state.resolve()))
        self.release(a['token']);self.release(b['token'])

    def test_boot_and_work_share_total_budget(self):
        os.environ['DYNAMIC_BOOT_DELAY']='1.2'
        marker=self.state/'started'
        p=self.cli('run','ios','--boot','--budget-seconds',5,'--json','--',sys.executable,'-c',f'from pathlib import Path;import time;Path({str(marker)!r}).touch();time.sleep(6)')
        self.assertEqual(p.returncode,124,p.stdout+p.stderr)
        self.assertTrue(marker.exists())
        self.assertEqual(self.status()['leases'],[])
        self.assertNotIn('token',json.loads(p.stdout))

    def test_short_budget_prevents_work_after_long_boot(self):
        os.environ['DYNAMIC_BOOT_DELAY']='2'
        marker=self.state/'forbidden'
        p=self.cli('run','ios','--boot','--budget-seconds',.45,'--json','--',sys.executable,'-c',f'from pathlib import Path;Path({str(marker)!r}).touch()')
        self.assertEqual(p.returncode,124,p.stdout+p.stderr);self.assertFalse(marker.exists())
        self.assertEqual(self.status()['leases'],[])

    def test_renewal_count_and_hard_deadline_cannot_be_extended(self):
        a=self.lease(ttl=1);m=Manager(self.state)
        hard=a['hard_expires']
        for _ in range(3):
            time.sleep(.02);r=m.renew(a['token'],1);self.assertEqual(r['hard_expires'],hard)
        with self.assertRaises(OwnershipError):m.renew(a['token'],2)
        self.assertLessEqual(m.get_lease(a['token'])['expires'],hard)
        m.release(a['token']);m.close()

    def test_noop_renewals_do_not_consume_allowance(self):
        a=self.lease();m=Manager(self.state)
        for _ in range(5):self.assertEqual(m.renew(a['token'])['renewals'],0)
        m.release(a['token']);m.close()

    def test_waiter_causes_safe_yield_then_opt_in_requeue_at_tail(self):
        # Generic GUI intentionally forces one physical slot.
        self.config['policy']['waiter_slice_seconds']=2;self.write()
        marker=self.state/'checkpoint'
        script=f'from pathlib import Path;import time;p=Path({str(marker)!r});'+'first=not p.exists();p.touch();time.sleep(6 if first else .1)'
        a=self.launch('run','gui','--session','a','--requeue-on-yield','--max-requeues',1,'--json','--',sys.executable,'-c',script)
        self.until(marker.exists)
        b=self.launch('run','gui','--session','b','--json','--',sys.executable,'-c','import time;time.sleep(.1)')
        outb,errb=b.communicate(timeout=15);self.assertEqual(b.returncode,0,outb+errb)
        outa,erra=a.communicate(timeout=15);self.assertEqual(a.returncode,0,outa+erra)
        self.assertEqual(json.loads(outa)['requeues'],1)
        order=[r['session'] for r in reversed(self.status()['events']) if r['event']=='acquired']
        self.assertEqual(order,['a','b','a']);self.assertEqual(self.status()['leases'],[])

    def test_yield_without_restart_permission_returns_75(self):
        self.config['policy']['waiter_slice_seconds']=1;self.write()
        a=self.launch('run','gui','--session','a','--json','--',sys.executable,'-c','import time;time.sleep(3)')
        self.until(lambda:any(r['operation']=='work' for r in self.status()['leases']))
        b=self.launch('run','gui','--session','b','--json','--',sys.executable,'-c','pass')
        out,err=a.communicate(timeout=15);self.assertEqual(a.returncode,75,out+err)
        self.assertTrue(json.loads(out)['requeue_required']);b.communicate(timeout=5)

    def test_pressure_hysteresis_pause_creation_drain_and_recover(self):
        settings=load_config(self.state/'config.json')['monitor']
        settings.update(high_load_ratio=.85,critical_load_ratio=1.25)
        bad={'time':1,'memory_free_percent':15,'load_ratio':1,'disk_free_gib':10}
        good={'time':2,'memory_free_percent':80,'load_ratio':.1,'disk_free_gib':10}
        controller=None
        for stage in (1,2,3):
            for _ in range(settings['bad_samples']):controller=advance(controller,bad,settings)
            self.assertEqual(controller['stage'],stage)
        for _ in range(settings['good_samples']-1):controller=advance(controller,good,settings)
        self.assertEqual(controller['stage'],3)
        controller=advance(controller,good,settings);self.assertEqual(controller['stage'],2)

    def test_critical_pressure_immediately_pauses_creation(self):
        settings=load_config(self.state/'config.json')['monitor']
        c=advance(None,{'time':1,'memory_free_percent':0,'load_ratio':0,'disk_free_gib':10},settings)
        self.assertEqual(c['stage'],1)

    def test_pressure_retains_active_private_environment(self):
        a=self.lease();m=Manager(self.state)
        m.db.execute("INSERT OR REPLACE INTO meta VALUES('controller',?)",(json.dumps({'stage':3,'sampled_at':time.time()+100}),))
        self.assertEqual(admission(m)['capacity'],1)
        self.assertIsNone(retire_idle(m,force=True))
        self.assertEqual(m.get_lease(a['token'])['resource'],a['resource_id'])
        with self.assertRaises(WaitTimeout):m.acquire('ios',session='new',owner_pid=os.getpid(),timeout=0)
        m.release(a['token']);again=m.acquire('ios',session='a',owner_pid=os.getpid(),timeout=0)
        self.assertEqual(again['mode'],'traditional');self.assertEqual(again['resource_id'],a['resource_id'])
        m.release(again['token']);m.close()

    def queued_request(self, m, session='a', pool='ios', project=None):
        stamp=process_stamp(os.getpid())
        m.db.execute('INSERT INTO queue(request,pool,session,project,owner_pid,owner_start,boot,waiter_pid,waiter_start,deadline,ttl,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                     (session+pool,pool,session,str(Path(project or Path.cwd()).resolve()),os.getpid(),stamp,m.machine_boot,os.getpid(),stamp,time.time()+30,10,time.time()))

    def warm_row(self, m, session='a'):
        a=m.acquire('ios',session=session,owner_pid=os.getpid(),timeout=0)
        m.release(a['token'])
        m.db.execute('UPDATE environments SET running=1 WHERE resource=?',(a['resource_id'],))
        return a['resource_id']

    def test_consecutive_supervised_chunks_reuse_warm_vm_without_reboot(self):
        for _ in range(2):
            p=self.cli('run','ios','--session','a','--project',str(ROOT),'--boot','--json','--',sys.executable,'-c','pass')
            self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        con=sqlite3.connect(self.state/'sdk.sqlite')
        calls=[json.loads(r[0]) for r in con.execute("SELECT args FROM calls WHERE name='xcrun'")];con.close()
        self.assertEqual(sum(c[:2]==['simctl','boot'] for c in calls),1)
        self.assertFalse(any(c[:2]==['simctl','shutdown'] for c in calls))

    def test_pressure_stage_within_capacity_keeps_unleased_warm_vm(self):
        m=Manager(self.state);self.warm_row(m)
        m.db.execute("INSERT OR REPLACE INTO meta VALUES('controller',?)",(json.dumps({'stage':3,'pressure':'neutral'}),))
        with patch('sim_manager.providers.stop_managed') as stop_vm:
            self.assertIsNone(retire_idle(m));stop_vm.assert_not_called()
        m.close()

    def test_same_owner_queue_protects_warm_vm_even_after_idle_timeout(self):
        m=Manager(self.state);r=self.warm_row(m)
        m.db.execute('UPDATE environments SET last_used=?',(time.time()-1000,))
        self.queued_request(m)
        with patch('sim_manager.providers.stop_managed') as stop_vm:
            self.assertIsNone(retire_idle(m));stop_vm.assert_not_called()
        m.db.execute('DELETE FROM queue');m.db.execute('DELETE FROM meta WHERE key="idle_check"')
        with patch('sim_manager.providers.stop_managed'):
            self.assertTrue(retire_idle(m)['stopped'])
        m.close()

    def test_unrelated_gui_queue_does_not_retire_warm_mobile_vm(self):
        m=Manager(self.state);self.warm_row(m);self.queued_request(m,'gui-owner','gui')
        with patch('sim_manager.providers.stop_managed') as stop_vm:
            self.assertIsNone(retire_idle(m));stop_vm.assert_not_called()
        m.close()

    def test_waiting_cold_owner_reclaims_slot_but_preserves_queued_warm_owner(self):
        m=Manager(self.state)
        ids=[self.warm_row(m,name) for name in ('a','b','c')]
        self.queued_request(m,'new');self.queued_request(m,'a')
        with patch('sim_manager.providers.stop_managed'):
            result=retire_idle(m)
        self.assertTrue(result['stopped']);self.assertNotEqual(result['resource_id'],ids[0])
        self.assertEqual(m.db.execute('SELECT running FROM environments WHERE resource=?',(ids[0],)).fetchone()[0],1)
        m.close()

    def test_foreground_waiter_blocked_by_gui_does_not_reclaim_mobile_slot(self):
        m=Manager(self.state)
        for name in ('a','b','c'):self.warm_row(m,name)
        gui=m.acquire('gui',session='gui-owner',owner_pid=os.getpid(),timeout=0)
        self.queued_request(m,'new')
        m.db.execute('UPDATE queue SET foreground=1')
        with patch('sim_manager.providers.stop_managed') as stop_vm:
            self.assertIsNone(retire_idle(m));stop_vm.assert_not_called()
        m.release(gui['token']);m.close()

    def test_pressure_reclaims_only_excess_warm_capacity(self):
        m=Manager(self.state);self.warm_row(m,'a');self.warm_row(m,'b')
        m.db.execute("INSERT OR REPLACE INTO meta VALUES('controller',?)",(json.dumps({'stage':3,'pressure':'neutral'}),))
        with patch('sim_manager.providers.stop_managed'):
            self.assertTrue(retire_idle(m)['stopped'])
            m.db.execute('DELETE FROM meta WHERE key="idle_check"')
            self.assertIsNone(retire_idle(m))
        m.close()

    def test_idle_shutdown_is_exact_owned_device_and_preserves_identity(self):
        p=self.cli('run','ios','--session','a','--boot','--json','--',sys.executable,'-c','pass')
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        resource=json.loads(p.stdout)['resource_id'];m=Manager(self.state)
        r=retire_idle(m,force=True);self.assertTrue(r['stopped'],r)
        a=m.acquire('ios',session='a',owner_pid=os.getpid(),timeout=0);self.assertEqual(a['resource_id'],resource)
        m.release(a['token']);m.close()
        con=sqlite3.connect(self.state/'sdk.sqlite');calls=[json.loads(r[0]) for r in con.execute("SELECT args FROM calls WHERE name='xcrun'")];con.close()
        shutdown=[c for c in calls if c[:2]==['simctl','shutdown']]
        self.assertEqual(shutdown,[['simctl','shutdown',a['resource']['udid']]])

    def test_leased_private_android_target_repair_preserves_data(self):
        from sim_manager.provision import repair_leased_android_target
        a=self.lease(pool='android');m=Manager(self.state)
        try:
            home=Path(a['resource']['avd_home']);name=a['resource']['avd'];manifest=home/(name+'.ini')
            manifest.write_text(manifest.read_text().replace('target=android-40','target=android-0'))
            data=home/(name+'.avd')/'userdata.img';data.write_bytes(b'preserve-this')
            with patch('sim_manager.provision.ports_free',return_value=True):result=repair_leased_android_target(m,a['token'])
            self.assertTrue(result['repaired']);self.assertEqual(result['target'],'android-40')
            self.assertEqual(data.read_bytes(),b'preserve-this');self.assertIn('target=android-40',manifest.read_text())
            self.assertEqual(m.get_lease(a['token'])['renewals'],0)
        finally:m.close();self.release(a['token'])

    def test_android_target_repair_rejects_unassigned_environment(self):
        from sim_manager.provision import repair_leased_android_target
        a=self.lease(pool='android');m=Manager(self.state)
        try:
            with m.transaction():m.db.execute('DELETE FROM environments WHERE resource=?',(a['resource_id'],))
            with self.assertRaises(ManagerError):repair_leased_android_target(m,a['token'])
        finally:m.close();self.release(a['token'])

    def test_android_target_repair_rejects_busy_vm_ports_and_external_image(self):
        from sim_manager.provision import repair_leased_android_target
        a=self.lease(pool='android');m=Manager(self.state)
        try:
            with patch('sim_manager.provision.ports_free',return_value=False):
                with self.assertRaises(ManagerError):repair_leased_android_target(m,a['token'])
            with m.transaction():m.db.execute('UPDATE environments SET running=1 WHERE resource=?',(a['resource_id'],))
            with self.assertRaises(ManagerError):repair_leased_android_target(m,a['token'])
            with m.transaction():m.db.execute('UPDATE environments SET running=0 WHERE resource=?',(a['resource_id'],))
            content=Path(a['resource']['avd_home'])/(a['resource']['avd']+'.avd')
            (content/'config.ini').write_text('image.sysdir.1=/outside/sdk\n')
            with patch('sim_manager.provision.ports_free',return_value=True):
                with self.assertRaises(ManagerError):repair_leased_android_target(m,a['token'])
        finally:m.close();self.release(a['token'])

    def test_zero_wait_attempt_survives_slow_local_housekeeping(self):
        from sim_manager import dynamic
        original=dynamic.retire_idle
        def slow(manager,*args,**kwargs):
            time.sleep(.2)
            return original(manager,*args,**kwargs)
        with patch('sim_manager.dynamic.retire_idle',side_effect=slow):
            a=self.lease()
        self.assertEqual(a['mode'],'dynamic')
        self.release(a['token'])
        self.assertEqual(self.status()['queue'],[])

    def test_committed_creation_is_preserved_and_recovered_after_delivery_failure(self):
        from sim_manager.dynamic import fail_environment
        a=self.lease();self.release(a['token']);m=Manager(self.state)
        fail_environment(m,a['resource_id'])
        self.assertEqual(m.db.execute('SELECT phase FROM environments').fetchone()[0],'ready')
        m.db.execute("UPDATE environments SET phase='failed'")
        retire_idle(m,force=True)
        self.assertEqual(m.db.execute('SELECT phase FROM environments').fetchone()[0],'ready')
        again=m.acquire('ios',session='a',owner_pid=os.getpid(),timeout=0)
        self.assertEqual(again['resource']['udid'],a['resource']['udid'])
        m.release(again['token']);m.close()

    def test_android_idle_shutdown_verifies_owned_pid_avd_and_explicit_serial(self):
        p=self.cli('run','android','--session','a','--boot','--json','--',sys.executable,'-c','pass')
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        m=Manager(self.state);row=m.db.execute("SELECT spec FROM environments WHERE pool='android'").fetchone();spec=json.loads(row[0])
        result=retire_idle(m,force=True);self.assertTrue(result['stopped'],result);m.close()
        con=sqlite3.connect(self.state/'sdk.sqlite')
        calls=[json.loads(r[0]) for r in con.execute("SELECT args FROM calls WHERE name='adb'")];con.close()
        self.assertIn(['-s','emulator-'+str(spec['port']),'emu','kill'],calls)
        self.assertFalse(any('kill-server' in c for c in calls))

    def test_interrupted_idle_shutdown_is_recovered_only_after_stopper_dies(self):
        p=self.cli('run','ios','--session','a','--boot','--json','--',sys.executable,'-c','pass')
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        m=Manager(self.state)
        m.db.execute("UPDATE environments SET phase='stopping',creator_pid=?,creator_start=?",(os.getpid(),process_stamp(os.getpid())))
        self.assertIsNone(retire_idle(m,force=True))
        m.db.execute("UPDATE environments SET creator_start='dead-start'")
        result=retire_idle(m,force=True);self.assertTrue(result['stopped'],result);m.close()

    def test_unknown_runtime_shutdown_refused(self):
        a=self.lease();self.release(a['token']);m=Manager(self.state)
        m.db.execute('UPDATE environments SET running=1 WHERE resource=?',(a['resource_id'],))
        r=retire_idle(m,force=True);self.assertFalse(r['stopped']);self.assertIn('provenance',r['reason'])
        self.assertEqual(m.db.execute('SELECT running FROM environments').fetchone()[0],1);m.close()

    def test_foreground_mobile_requests_are_serialized_without_nested_gui_lease(self):
        a=self.lease(foreground=True)
        with self.assertRaises(WaitTimeout):self.lease('b',foreground=True)
        b=self.lease('b');self.release(b['token']);self.release(a['token'])

    def test_watcher_singleton_and_restart_after_crash(self):
        self.config['monitor']['daemon']=True;self.write();m=Manager(self.state)
        ensure(m)
        def info():
            row=m.db.execute("SELECT value FROM meta WHERE key='watcher'").fetchone();return json.loads(row[0]) if row else None
        first=self.until(info);self.assertEqual(ensure(m)['pid'],first['pid'])
        os.kill(first['pid'],signal.SIGKILL)
        self.until(lambda:process_stamp(first['pid']) is None)
        ensure(m);second=self.until(lambda:info() if info() and info()['pid']!=first['pid'] else None)
        self.assertNotEqual(first['pid'],second['pid']);stop(m);m.close()

    def test_ensure_restarts_watcher_with_old_identity_protocol(self):
        self.config['monitor']['daemon']=True;self.write();m=Manager(self.state)
        ensure(m)
        def info():
            row=m.db.execute("SELECT value FROM meta WHERE key='watcher'").fetchone();return json.loads(row[0]) if row else None
        first=self.until(info)
        legacy=dict(first);legacy.pop('identity_version',None)
        m.db.execute("INSERT OR REPLACE INTO meta VALUES('watcher',?)",(json.dumps(legacy),))
        ensure(m)
        updated=self.until(lambda:info() if info() and info()['pid']!=first['pid'] else None)
        self.assertEqual(updated['identity_version'],2)
        stop(m);m.close()

    def test_watchdog_enforces_dead_supervisor_budget_without_killing_owner_session(self):
        a=self.launch('run','gui','--budget-seconds',.7,'--json','--',sys.executable,'-c','import time;time.sleep(20)')
        self.until(lambda:self.status()['leases'] and self.status()['leases'][0]['operation']=='work')
        a.kill();a.wait(timeout=2);time.sleep(.75)
        m=Manager(self.state);tick(m);self.assertEqual(m.status()['leases'],[]);m.close()
        a.communicate(timeout=5)

    def test_host_reboot_marks_private_runtime_stopped(self):
        a=self.lease();self.release(a['token']);m=Manager(self.state)
        m.db.execute("UPDATE environments SET running=1,boot='old-boot'")
        retire_idle(m,force=True);self.assertEqual(m.db.execute('SELECT running FROM environments').fetchone()[0],0);m.close()

if __name__=='__main__':unittest.main()
