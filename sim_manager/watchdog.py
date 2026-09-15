"""One user-local watcher per shared state; never signal a Codex session PID."""
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from .core import ManagerError, group_alive, process_alive, process_stamp

_children = []  # Keep detached children until they can be reaped without blocking.


def stop(manager):
    row = manager.db.execute("SELECT value FROM meta WHERE key='watcher'").fetchone()
    if not row:
        return {'stopped':False}
    info = json.loads(row[0])
    if info['boot']==manager.machine_boot and process_alive(info['pid'],info['start']):
        os.kill(info['pid'],signal.SIGTERM)
        deadline = time.monotonic()+5
        while time.monotonic()<deadline and process_alive(info['pid'],info['start']):
            time.sleep(.05)
        if process_alive(info['pid'],info['start']):
            raise ManagerError('Watcher did not stop; upgrade deferred')
    for child in _children[:]:
        child.poll()
        if child.returncode is not None:
            _children.remove(child)
    return {'stopped':True}


def ensure(manager):
    for child in _children[:]:
        if child.poll() is not None:
            _children.remove(child)
    if not manager.config['monitor']['daemon']:
        return {'enabled':False}
    row = manager.db.execute("SELECT value FROM meta WHERE key='watcher'").fetchone()
    info = json.loads(row[0]) if row else None
    if info and info['boot']==manager.machine_boot and process_alive(info['pid'],info['start']):
        return {'enabled':True,'pid':info['pid']}
    logs = manager.state_dir/'logs'
    logs.mkdir(exist_ok=True,mode=0o700)
    with (logs/'watcher.log').open('ab') as log:
        child = subprocess.Popen([sys.executable,'-m','sim_manager','watch','--state-dir',str(manager.state_dir),
                                  '--config',str(manager.config_path.resolve())],start_new_session=True,
                                  stdin=subprocess.DEVNULL,stdout=log,stderr=log)
    _children.append(child)
    # The watcher itself holds flock. Competing starts exit without touching VMs.
    return {'enabled':True,'starting_pid':child.pid}


def enforce_orphans(manager):
    for r in manager.db.execute('SELECT * FROM leases WHERE activity_group IS NOT NULL').fetchall():
        if manager.owner_alive(r) or not manager.activity_alive(r):
            continue
        budget = manager.budget(r['token'])
        if budget['remaining_seconds']>0:
            continue
        # A present leader with a different start stamp is a reused PID: fail closed.
        stamp = process_stamp(r['activity_group'])
        if stamp and stamp!=r['activity_start']:
            manager.event('orphan-stop-refused',r['resource'],r['session'],'Process identity changed')
            continue
        for sig in (signal.SIGTERM,signal.SIGKILL):
            if not group_alive(r['activity_group'],r['boot']):
                break
            try:
                os.killpg(r['activity_group'],sig)
            except ProcessLookupError:
                break
            if sig==signal.SIGTERM:
                deadline = time.monotonic()+3
                while time.monotonic()<deadline and group_alive(r['activity_group'],r['boot']):
                    time.sleep(.05)
        with manager.transaction():
            manager.sweep()


def tick(manager):
    from . import monitor, dynamic
    monitor.update(manager)
    enforce_orphans(manager)
    retired = dynamic.retire_idle(manager)
    with manager.transaction():
        reaped = manager.sweep()
    return {'reaped':reaped,'retired':retired,'scheduler':monitor.admission(manager)}


def watch(manager, once=False):
    if once:
        return tick(manager)
    with (manager.state_dir/'watcher.lock').open('a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            return {'watching':False,'reason':'already-running'}
        info = {'pid':os.getpid(),'start':process_stamp(os.getpid()),'boot':manager.machine_boot}
        with manager.transaction():
            manager.db.execute("INSERT OR REPLACE INTO meta VALUES('watcher',?)",(json.dumps(info),))
        try:
            while (manager.state_dir/'state.sqlite3').is_file() and manager.config_path.is_file():
                # Reload changed configuration only after the scheduler is drained.
                from .core import load_config
                with manager.transaction():
                    if not manager.db.execute('SELECT 1 FROM leases UNION ALL SELECT 1 FROM queue LIMIT 1').fetchone():
                        manager.config = load_config(manager.config_path)
                        manager.requested = manager.config
                        manager.db.execute("INSERT OR REPLACE INTO meta VALUES('config',?)",(manager.canonical(manager.config),))
                tick(manager)
                time.sleep(manager.config['monitor']['sample_seconds'])
        finally:
            with manager.transaction():
                manager.db.execute("DELETE FROM meta WHERE key='watcher' AND value=?",(json.dumps(info),))
    return {'watching':False}
