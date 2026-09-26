import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sim_manager.core import Manager, ManagerError, OwnershipError, load_config


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='sim manager test ')
        self.state = Path(self.tmp.name)
        self.conf = self.state/'config.json'
        self.config = {'version':1,'global_capacity':2,'lease_seconds':5,'poll_seconds':.025,
                       'pools':{'ios':{'capacity':1,'resources':[{'id':'i1','kind':'generic'},{'id':'i2','kind':'generic'}]},
                                'android':{'capacity':1,'resources':[{'id':'a1','kind':'generic'}]}}}
        self.write_config()
        self.children = []

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)
        self.tmp.cleanup()

    def write_config(self):
        self.conf.write_text(json.dumps(self.config))

    def manager(self):
        return Manager(self.state)

    def cli(self, *args, **kwargs):
        return subprocess.run([sys.executable,str(ROOT/'bin/sim-manager'),'--state-dir',str(self.state),*map(str,args)],
                              capture_output=True,text=True,timeout=kwargs.pop('timeout',10),**kwargs)

    def launch(self, *args):
        p = subprocess.Popen([sys.executable,str(ROOT/'bin/sim-manager'),'--state-dir',str(self.state),*map(str,args)],
                             stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.children.append(p)
        return p

    def until(self, condition, timeout=5):
        end = time.monotonic()+timeout
        while time.monotonic() < end:
            value = condition()
            if value:
                return value
            time.sleep(.025)
        self.fail('Timed out waiting for condition')

    def status(self):
        m = self.manager()
        try:
            return m.status()
        finally:
            m.close()

    def acquire(self, pool='ios', **kw):
        m = self.manager()
        try:
            return m.acquire(pool,owner_pid=os.getpid(),timeout=0,**kw)
        finally:
            m.close()

    def release(self, token):
        m = self.manager()
        try:
            return m.release(token)
        finally:
            m.close()

    def test_shell_json_tokens_and_idempotent_release(self):
        p = self.cli('acquire','ios','--owner-pid',os.getpid(),'--shell','--session',"x ' $(bad)")
        self.assertEqual(p.returncode,0,p.stderr)
        # Verify actual shell round-trip, not just quoting implementation.
        shell = subprocess.run(['/bin/sh','-c',p.stdout+'\nprintf "%s" "$SIM_MANAGER_TOKEN"'],capture_output=True,text=True)
        token = shell.stdout
        self.assertTrue(token)
        status = self.cli('status','--json')
        self.assertNotIn(token,status.stdout)
        self.assertFalse(self.release('i1')['released'])
        self.assertEqual(len(self.status()['leases']),1)
        self.assertTrue(self.release(token)['released'])
        self.assertFalse(self.release(token)['released'])

    def test_group_acquire_is_atomic_and_shell_exposes_all_devices(self):
        self.config['pools']['ios']['capacity'] = 2
        self.write_config()
        result = self.cli('acquire','ios','--count','2','--owner-pid',os.getpid(),'--json')
        self.assertEqual(result.returncode,0,result.stderr)
        group = json.loads(result.stdout)
        self.assertEqual([r['resource_id'] for r in group['leases']],['i1','i2'])
        self.assertEqual(len(self.status()['leases']),2)
        blocked = self.cli('acquire','ios','--count','2','--owner-pid',os.getpid(),'--timeout','0','--json')
        self.assertNotEqual(blocked.returncode,0)
        self.assertEqual(len(self.status()['leases']),2)
        for lease in group['leases']:
            self.release(lease['token'])
        shell = self.cli('acquire','ios','--count','2','--owner-pid',os.getpid(),'--shell')
        self.assertEqual(shell.returncode,0,shell.stderr)
        values = subprocess.run(['/bin/sh','-c',shell.stdout+'\nprintf "%s|%s" "$SIM_MANAGER_COUNT" "$SIM_MANAGER_RESOURCE_IDS"'],
                                capture_output=True,text=True)
        self.assertEqual(values.stdout,'2|i1,i2')
        tokens = subprocess.run(['/bin/sh','-c',shell.stdout+'\nprintf "%s" "$SIM_MANAGER_TOKENS"'],
                                capture_output=True,text=True).stdout.split(',')
        for token in tokens:
            self.release(token)

    def test_group_run_releases_every_device_after_failure(self):
        self.config['pools']['ios']['capacity'] = 2
        self.write_config()
        result = self.cli('run','ios','--count','2','--session','multiplayer','--json','--',
                          sys.executable,'-c','import os,sys; assert os.environ["SIM_MANAGER_COUNT"]=="2"; sys.exit(7)')
        self.assertEqual(result.returncode,7,result.stderr)
        self.assertEqual(json.loads(result.stdout)['count'],2)
        self.assertEqual(self.status()['leases'],[])

    def test_group_waits_under_shared_host_pressure_without_partial_lease(self):
        self.config['pools']['ios']['capacity'] = 2
        self.write_config()
        m = self.manager()
        with m.transaction():
            m.db.execute("INSERT OR REPLACE INTO meta VALUES('controller',?)",
                         (json.dumps({'stage':3,'pressure':'elevated','sampled_at':time.time()}),))
        m.close()
        refused = self.cli('acquire','ios','--count','2','--owner-pid',os.getpid(),'--timeout','0','--json')
        self.assertEqual(refused.returncode,3,refused.stdout+refused.stderr)
        self.assertEqual(self.status()['leases'],[])

    def test_multiplayer_fifo_head_prevents_partial_or_later_single_grant(self):
        self.config['pools']['ios']['capacity'] = 2
        self.write_config()
        held = self.acquire()
        group = self.launch('acquire','ios','--count','2','--owner-pid',os.getpid(),'--session','group','--json')
        self.until(lambda: len(self.status()['queue']) == 1)
        single = self.launch('acquire','ios','--owner-pid',os.getpid(),'--session','later','--json')
        self.until(lambda: len(self.status()['queue']) == 2)
        self.assertEqual([row['count'] for row in self.status()['queue']], [2,1])
        self.assertEqual(len(self.status()['leases']),1)
        self.release(held['token'])
        group_out,group_err = group.communicate(timeout=5)
        self.assertEqual(group.returncode,0,group_err)
        self.assertIsNone(single.poll())
        leases=json.loads(group_out)['leases']
        self.assertEqual(len(leases),2)
        for lease in leases:
            self.release(lease['token'])
        single_out,single_err = single.communicate(timeout=5)
        self.assertEqual(single.returncode,0,single_err)
        self.release(json.loads(single_out)['token'])

    def test_recent_release_includes_task_platform_and_total_occupancy(self):
        project = self.state/'mobile-project'
        m = self.manager()
        try:
            m.enable(session='task-activity',project=str(project))
            lease = m.acquire('ios',session='task-activity',project=str(project),
                              owner_pid=os.getpid(),timeout=0,mode='traditional')
            time.sleep(.02)
            m.release(lease['token'])
            released = next(e for e in m.status()['events'] if e['event']=='released')
        finally:
            m.close()
        self.assertEqual(released['session'],'task-activity')
        self.assertEqual(released['project'],str(project.resolve()))
        self.assertEqual(released['pool'],'ios')
        self.assertEqual(released['resource'],'i1')
        self.assertGreaterEqual(released['duration_seconds'],.02)
        self.assertLessEqual(released['started_at'],released['time'])

    def test_capacity_and_timeout(self):
        lease = self.acquire()
        other = self.acquire('android')
        p = self.cli('acquire','ios','--timeout',.15,'--json')
        self.assertEqual(p.returncode,3,p.stdout+p.stderr)
        self.assertEqual(self.status()['queue'],[])
        self.release(lease['token'])
        self.release(other['token'])

    def test_fifo_order_across_processes(self):
        lease = self.acquire()
        waiters = []
        for n in range(4):
            waiters.append(self.launch('acquire','ios','--timeout',30,'--owner-pid',os.getpid(),'--session',f'w{n}','--json'))
            self.until(lambda:len(self.status()['queue'])==n+1)
        self.assertEqual([r['session'] for r in self.status()['queue']],['w0','w1','w2','w3'])
        self.release(lease['token'])
        for n,p in enumerate(waiters):
            out,err = p.communicate(timeout=15)
            self.assertEqual(p.returncode,0,err)
            acquired = json.loads(out)
            self.assertEqual(acquired['session'],f'w{n}')
            self.assertEqual(len(self.status()['leases']),1)
            self.release(acquired['token'])
        self.assertEqual(self.status()['leases'],[])

    def test_crashed_waiter_removed(self):
        lease = self.acquire()
        p = self.launch('acquire','ios','--timeout',8,'--owner-pid',os.getpid())
        self.until(lambda:len(self.status()['queue'])==1)
        p.kill(); p.communicate()
        self.assertEqual(self.status()['queue'],[])
        self.release(lease['token'])

    def test_dead_owner_auto_reclaimed(self):
        owner = subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)'])
        self.children.append(owner)
        p = self.cli('acquire','ios','--owner-pid',owner.pid,'--json')
        self.assertEqual(p.returncode,0,p.stderr)
        owner.kill(); owner.communicate()
        cleanup = self.cli('cleanup','--json')
        self.assertEqual(cleanup.returncode,0,cleanup.stdout+cleanup.stderr)
        self.assertEqual(json.loads(cleanup.stdout)['reaped'],['i1'])
        lease = self.acquire()
        self.assertEqual(lease['resource_id'],'i1')
        self.release(lease['token'])

    def test_pid_reuse_and_reboot_stamps(self):
        lease = self.acquire()
        m = self.manager()
        with m.transaction():
            m.db.execute("UPDATE leases SET owner_start='different start' WHERE token=?",(lease['token'],))
        self.assertEqual(m.cleanup()['reaped'],['i1'])
        m.close()
        lease = self.acquire()
        m = self.manager()
        with m.transaction():
            m.db.execute("UPDATE leases SET boot='older boot' WHERE token=?",(lease['token'],))
        self.assertEqual(m.cleanup()['reaped'],['i1'])
        m.close()

    def test_expired_live_owner_is_not_stolen_and_must_requeue(self):
        lease = self.acquire(ttl=1)
        m = self.manager()
        with m.transaction():
            m.db.execute('UPDATE leases SET expires=?,hard_expires=? WHERE token=?',
                         (time.time()-1,time.time()-1,lease['token']))
        m.close()
        s = self.status()
        self.assertTrue(s['leases'][0]['expired'])
        self.assertEqual(self.cli('acquire','ios','--timeout',0).returncode,3)
        m = self.manager()
        with self.assertRaises(OwnershipError):
            m.begin_activity(lease['token'],'work')
        with self.assertRaises(OwnershipError):
            m.renew(lease['token'])
        m.close()
        self.release(lease['token'])

    def test_run_success_failure_and_timeout_release(self):
        for command,expected in [([sys.executable,'-c','import os; print(os.environ["SIM_MANAGER_RESOURCE_ID"])'],0),
                                 ([sys.executable,'-c','raise SystemExit(7)'],7),
                                 ([sys.executable,'-c','import time; time.sleep(2)'],124)]:
            p = self.cli('run','ios','--command-timeout',.35,'--json','--',*command)
            self.assertEqual(p.returncode,expected,p.stdout+p.stderr)
            self.assertEqual(json.loads(p.stdout)['exit_code'],expected)
            self.assertEqual(self.status()['leases'],[])

    def test_sigterm_releases(self):
        p = self.launch('run','ios','--',sys.executable,'-c','import time; time.sleep(10)')
        self.until(lambda:self.status()['leases'] and self.status()['leases'][0]['operation']=='work')
        p.terminate()
        out,err=p.communicate(timeout=6)
        self.assertEqual(p.returncode,130,out+err)
        self.assertEqual(self.status()['leases'],[])

    def test_sigkill_retains_live_child_then_reaps(self):
        p = self.launch('run','ios','--',sys.executable,'-c','import time; time.sleep(1.5)')
        self.until(lambda:self.status()['leases'] and self.status()['leases'][0]['operation']=='work')
        p.kill()
        # communicate waits for inherited pipes until the child finishes, so
        # inspect reservation before draining pipes.
        p.wait(timeout=2)
        self.assertEqual(len(self.status()['leases']),1)
        self.assertEqual(self.cli('acquire','ios','--timeout',0).returncode,3)
        self.until(lambda:not self.status()['leases'])
        p.communicate(timeout=3)

    def test_surviving_descendant_retains_reservation(self):
        p = self.launch('run','ios','--command-timeout',3,'--','/bin/sh','-c','sleep 1.3 & exit 0')
        self.until(lambda:self.status()['leases'] and self.status()['leases'][0]['operation']=='work')
        p.kill();p.wait(timeout=2)
        self.assertEqual(len(self.status()['leases']),1)
        self.until(lambda:not self.status()['leases'])
        p.communicate(timeout=3)

    def test_owner_and_boot_identity_are_timezone_invariant(self):
        from sim_manager.core import boot_id,process_stamp
        with patch.dict(os.environ,{'TZ':'UTC'}):
            boot=boot_id();stamp=process_stamp(os.getpid());lease=self.acquire()
        with patch.dict(os.environ,{'TZ':'America/Los_Angeles'}):
            self.assertEqual(boot_id(),boot)
            self.assertEqual(process_stamp(os.getpid()),stamp)
            self.assertEqual(len(self.status()['leases']),1)
            self.release(lease['token'])

    def test_macos_boot_identity_uses_kernel_uuid_not_clock_estimate(self):
        from sim_manager.core import boot_id
        with patch('sim_manager.core.sys.platform','darwin'),patch('sim_manager.core.subprocess.check_output',return_value='9FA446AC-FE7B-4781-8878-F4756F39304B\n') as query:
            first=boot_id();second=boot_id()
            self.assertEqual(first,second)
            self.assertTrue(all(c.args[0]==['/usr/sbin/sysctl','-n','kern.bootsessionuuid'] for c in query.call_args_list))

    def test_legacy_live_identity_is_quarantined_if_clock_rendering_changes(self):
        lease=self.acquire();m=self.manager()
        m.db.execute("UPDATE leases SET boot='{ sec = 1, usec = 0 } changed clock' WHERE token=?",(lease['token'],))
        self.assertEqual(m.cleanup()['reaped'],[])
        self.assertTrue(m.release(lease['token'])['released']);m.close()

    def test_interrupt_after_begin_rolls_back_before_release(self):
        lease=self.acquire();m=self.manager();real=m.db
        class InterruptingConnection:
            def __init__(self):self.once=True
            def execute(self,sql,*args):
                result=real.execute(sql,*args)
                if sql=='BEGIN IMMEDIATE' and self.once:
                    self.once=False
                    raise KeyboardInterrupt
                return result
            @property
            def in_transaction(self):return real.in_transaction
        m.db=InterruptingConnection()
        with self.assertRaises(KeyboardInterrupt):
            with m.transaction():pass
        self.assertFalse(real.in_transaction)
        self.assertTrue(m.release(lease['token'])['released'])
        m.db=real;m.close()

    def test_gate_eof_never_runs_command(self):
        from sim_manager.execution import spawn_gated
        marker = self.state/'should-not-exist'
        env={**os.environ,'PYTHONPATH':str(ROOT)}
        child,gate = spawn_gated([sys.executable,'-c',f'from pathlib import Path; Path({str(marker)!r}).touch()'],env)
        os.close(gate)
        self.assertEqual(child.wait(timeout=3),125)
        self.assertFalse(marker.exists())

    def test_run_rejects_runtime_shutdown_before_acquiring(self):
        ios = self.cli('run','ios','--session','task','--project','/tmp/project','--json',
                       '--','sh','-c','xcrun simctl shutdown "$SIM_MANAGER_UDID"')
        self.assertNotEqual(ios.returncode,0)
        self.assertIn('forbidden `simctl shutdown`',json.loads(ios.stdout)['error'])
        android = self.cli('run','android','--session','task','--project','/tmp/project','--json',
                           '--','adb','-s','emulator-5554','emu','kill')
        self.assertNotEqual(android.returncode,0)
        self.assertIn('forbidden `adb emu kill`',json.loads(android.stdout)['error'])
        self.assertEqual(self.status()['leases'],[])
        self.assertEqual(self.status()['queue'],[])

    def test_busy_release_rejected(self):
        lease = self.acquire()
        m = self.manager()
        m.begin_activity(lease['token'],'manual')
        with self.assertRaises(OwnershipError):
            m.release(lease['token'])
        m.end_activity(lease['token'])
        m.release(lease['token']);m.close()

    def test_config_change_drains_without_blocking_release(self):
        lease = self.acquire()
        self.config['global_capacity']=1
        self.write_config()
        self.assertTrue(self.status()['config_pending'])
        self.assertEqual(self.cli('acquire','android','--timeout',0).returncode,1)
        self.release(lease['token'])
        self.assertFalse(self.status()['config_pending'])

    def test_invalid_or_missing_config_still_allows_release_status_cleanup(self):
        lease = self.acquire()
        self.conf.write_text('{broken')
        status = self.cli('status','--json')
        self.assertEqual(status.returncode,0,status.stdout+status.stderr)
        self.assertTrue(json.loads(status.stdout)['config_error'])
        self.assertEqual(self.cli('acquire','ios','--timeout',0).returncode,1)
        self.conf.unlink()
        p = self.cli('release',lease['token'],'--json')
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertTrue(json.loads(p.stdout)['released'])
        self.assertEqual(self.cli('cleanup','--json').returncode,0)

    def test_global_budget_cost_and_no_cross_pool_head_blocking(self):
        self.config['global_capacity']=1
        self.write_config()
        lease = self.acquire()
        p = self.launch('acquire','android','--timeout',5,'--owner-pid',os.getpid(),'--json')
        self.until(lambda:len(self.status()['queue'])==1)
        self.release(lease['token'])
        out,err=p.communicate(timeout=5)
        self.assertEqual(p.returncode,0,err)
        self.release(json.loads(out)['token'])

    def test_blocked_other_pool_head_does_not_idle_free_pool(self):
        held = self.acquire('android')
        waiter = self.launch('acquire','android','--timeout',5,'--owner-pid',os.getpid(),'--json')
        self.until(lambda:len(self.status()['queue'])==1)
        ios = self.acquire('ios')
        self.assertEqual(len(self.status()['leases']),2)
        self.release(ios['token'])
        self.release(held['token'])
        out,err=waiter.communicate(timeout=5)
        self.assertEqual(waiter.returncode,0,err)
        self.release(json.loads(out)['token'])

    def test_weighted_global_budget(self):
        for r in self.config['pools']['ios']['resources']:
            r['cost']=2
        self.write_config()
        ios=self.acquire()
        self.assertEqual(self.cli('acquire','android','--timeout',0).returncode,3)
        self.release(ios['token'])
        android=self.acquire('android')
        self.assertEqual(self.cli('acquire','ios','--timeout',0).returncode,3)
        self.release(android['token'])

    def test_invalid_config_duplicates_and_nonfinite(self):
        samples = [
            {'id':'dup','kind':'ios','udid':'U'},
            {'id':'dup','kind':'android','avd':'A','port':5556}]
        for resource in samples:
            self.config['pools']['ios']['resources']=[resource]
            self.config['pools']['android']['resources']=[{**resource,'id':'other'}]
            self.write_config()
            with self.assertRaises(ManagerError):load_config(self.conf)
        self.config['pools']['android']['resources']=[]
        self.config['pools']['ios']['capacity']=1.5
        self.write_config()
        with self.assertRaises(ManagerError):load_config(self.conf)
        self.config['pools']['ios']['capacity']=1
        self.config['tools']={'adb': []}
        self.write_config()
        with self.assertRaises(ManagerError):load_config(self.conf)
        del self.config['tools']
        self.config['global_capacity']=float('nan')
        self.write_config()
        with self.assertRaises(ManagerError):load_config(self.conf)

    def test_parallel_exclusion_observed_intervals(self):
        # Two independent physical slots, global budget and per-pool capacity=2.
        self.config['pools']['ios']['capacity']=2
        self.config['lease_seconds']=30  # Measure exclusion, not slow CI startup TTL.
        self.write_config()
        log=self.state/'intervals'
        command = ('import os,time,json; p='+repr(str(log))+'; '
                   'r=os.environ["SIM_MANAGER_RESOURCE_ID"]; '
                   'f=open(p,"a"); f.write(json.dumps(["start",r,time.time()])+"\\n"); f.flush(); '
                   'time.sleep(.12); f.write(json.dumps(["end",r,time.time()])+"\\n"); f.close()')
        ps=[self.launch('run','ios','--timeout',30,'--',sys.executable,'-c',command) for _ in range(8)]
        for p in ps:
            out,err=p.communicate(timeout=40)
            self.assertEqual(p.returncode,0,err)
        active=set()
        for event,r,t in sorted((json.loads(line) for line in log.read_text().splitlines()),key=lambda row:row[2]):
            if event=='start':
                self.assertNotIn(r,active)
                active.add(r)
                self.assertLessEqual(len(active),2)
            else:
                self.assertIn(r,active);active.remove(r)
        self.assertEqual(active,set())
        self.assertEqual(self.status()['leases'],[])


if __name__=='__main__':unittest.main()
