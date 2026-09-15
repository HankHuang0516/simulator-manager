"""Persistent per-session devices with bounded, pressure-aware running occupancy."""
import hashlib
import json
import os
import time
from .core import ManagerError, process_alive, boot_matches
from .monitor import admission


def candidate(manager,pool,leases,request):
    control = admission(manager)
    cost = sum(json.loads(r['spec'])['cost'] for r in leases)
    if cost>=control['capacity']:
        return []
    foreground = request['foreground'] or pool=='gui'
    if foreground and any(r['foreground'] or r['pool']=='gui' for r in leases):
        return []
    dynamic = manager.config['mode']=='dynamic' and pool in ('ios','android') and request['requested_mode']!='traditional'
    if not dynamic:
        # Traditional admission still observes the host-wide pressure limit.
        return manager.candidates(pool,leases)
    row = manager.db.execute('SELECT * FROM environments WHERE session=? AND project=? AND pool=?',
                             (request['session'],request['project'],pool)).fetchone()
    if row:
        if row['phase']!='ready' or any(r['resource']==row['resource'] for r in leases):
            return []
        spec = json.loads(row['spec'])
        running = manager.db.execute('SELECT COUNT(*) FROM environments WHERE running=1 OR phase IN (\'creating\',\'stopping\') OR resource IN (SELECT resource FROM leases)').fetchone()[0]
        if not row['running'] and running>=control['capacity']:
            return []
        spec['_mode'] = 'dynamic' if control['stage']<=1 else 'traditional'
        return [spec]
    if not control['creation_allowed']:
        # No personal environment yet: a configured shared device can be used
        # temporarily, without assigning another session's private device.
        return manager.candidates(pool,leases)
    running = manager.db.execute("SELECT COUNT(*) FROM environments WHERE running=1 OR phase IN ('creating','stopping') OR resource IN (SELECT resource FROM leases)").fetchone()[0]
    if running>=control['capacity'] or manager.db.execute('SELECT COUNT(*) FROM environments').fetchone()[0]>=manager.config['dynamic']['max_environments']:
        return []
    identity = hashlib.sha256((request['session']+'\0'+request['project']+'\0'+pool).encode()).hexdigest()[:24]
    spec = {'id':'dynamic-'+pool+'-'+identity,'kind':pool,'cost':1,'enabled':True,'allow_attach':False,'_create':True,'_mode':'dynamic'}
    if pool=='android':
        from .providers import ports_free
        used = {r['port'] for p in manager.config['pools'].values() for r in p['resources'] if r['kind']=='android'}
        used.update(json.loads(r[0])['port'] for r in manager.db.execute("SELECT spec FROM environments WHERE pool='android'") if 'port' in json.loads(r[0]))
        port = next((p for p in range(5556,5683,2) if p not in used and ports_free(p)),None)
        if port is None:
            return []
        spec['port'] = port  # Reservation occurs in the same allocation transaction.
    return [spec]


def create_environment(manager,token,spec,pool):
    from .execution import execute
    import sys
    rc = execute(manager,token,[sys.executable,'-m','sim_manager','_create',token,
                 '--state-dir',str(manager.state_dir),'--config',str(manager.config_path.resolve())],
                 timeout=120,operation='create',stdout=sys.stderr,stderr=sys.stderr)
    if rc:
        error = ManagerError('Environment creation failed or budget ended; retry after checking installed SDKs')
        error.code = rc
        raise error


def provision_environment(manager,token):
    from .provision import ios_resource, android_resource
    lease = manager.get_lease(token)
    placeholder = json.loads(lease['spec'])
    actual = ios_resource(manager) if lease['pool']=='ios' else android_resource(manager,placeholder['port'],manager.state_dir/'environments'/placeholder['id']/'avds')
    actual['id'] = placeholder['id']
    with manager.transaction():
        manager.get_lease(token)  # Creation time is part of the original budget.
        manager.db.execute("UPDATE environments SET spec=?,phase='ready',creator_pid=NULL,creator_start=NULL WHERE resource=?",(json.dumps(actual),actual['id']))
        manager.db.execute('UPDATE leases SET spec=? WHERE token=?',(json.dumps(actual),token))
        manager.event('environment-created',actual['id'],lease['session'])


def fail_environment(manager,resource):
    with manager.transaction():
        # Never adopt partially created devices; operators can inspect any orphans.
        manager.db.execute("UPDATE environments SET phase='failed',creator_pid=NULL,creator_start=NULL WHERE resource=? AND phase='creating'",(resource,))


def retire_idle(manager,force=False):
    """Stop at most one unleased, provenance-verified private VM per call."""
    from .providers import stop_managed
    now = time.time()
    with manager.transaction():
        # Complete specs are durable SDK-create acknowledgments, never name adoption.
        for complete in manager.db.execute("SELECT * FROM environments WHERE phase='failed' AND running=0 AND resource NOT IN (SELECT resource FROM leases)").fetchall():
            spec = json.loads(complete['spec'])
            acknowledged = bool(spec.get('udid')) if complete['pool']=='ios' else bool(spec.get('avd') and spec.get('avd_home'))
            if acknowledged:
                manager.db.execute("UPDATE environments SET phase='ready',creator_pid=NULL,creator_start=NULL WHERE resource=?",(complete['resource'],))
                manager.event('environment-create-recovered',complete['resource'],complete['session'])
        # Crash recovery for incomplete creation leaves a visible, quarantined row.
        for row in manager.db.execute("SELECT * FROM environments WHERE phase='creating'").fetchall():
            if not boot_matches(row['boot'],manager.machine_boot) or not process_alive(row['creator_pid'] or 0,row['creator_start']):
                lease = manager.db.execute('SELECT * FROM leases WHERE resource=?',(row['resource'],)).fetchone()
                if not lease or not manager.activity_alive(lease):
                    manager.db.execute("UPDATE environments SET phase='failed' WHERE resource=?",(row['resource'],))
        for envrow in manager.db.execute("SELECT resource,boot FROM environments WHERE phase IN ('ready','stopping')").fetchall():
            if not boot_matches(envrow['boot'],manager.machine_boot):
                manager.db.execute("UPDATE environments SET running=0,phase='ready',boot=? WHERE resource=?",(manager.machine_boot,envrow['resource']))
        last = manager.db.execute("SELECT value FROM meta WHERE key='idle_check'").fetchone()
        if not force and last and now-float(last[0])<manager.config['monitor']['sample_seconds']:
            return None
        manager.db.execute("INSERT OR REPLACE INTO meta VALUES('idle_check',?)",(str(now),))
        control = admission(manager)
        requests = manager.db.execute('SELECT * FROM queue ORDER BY seq').fetchall()
        environments = manager.db.execute('SELECT * FROM environments').fetchall()
        leases = manager.db.execute('SELECT * FROM leases').fetchall()
        leased = {r['resource'] for r in leases}
        running = sum(r['running'] or r['phase'] in ('creating','stopping') or r['resource'] in leased
                      for r in environments)
        private_requests = [q for q in requests if manager.config['mode']=='dynamic'
                            and q['pool'] in ('ios','android') and q['requested_mode']!='traditional']
        assigned = {(r['session'],r['project'],r['pool']):r for r in environments}
        # A pending request for the same warm assignment must reach admission
        # before maintenance can retire it, even after the idle timer elapsed.
        protected = {r['resource'] for q in private_requests
                     for r in [assigned.get((q['session'],q['project'],q['pool']))]
                     if r and r['phase']=='ready' and r['running']}
        needs_slot = False
        if running>=control['capacity'] and sum(json.loads(r['spec'])['cost'] for r in leases)<control['capacity']:
            heads = {q['pool']:q for q in reversed(requests)}
            for q in private_requests:
                if q['seq']!=heads[q['pool']]['seq']:
                    continue
                if q['foreground'] and any(r['foreground'] or r['pool']=='gui' for r in leases):
                    continue
                r = assigned.get((q['session'],q['project'],q['pool']))
                if r and r['phase']=='ready' and not r['running'] and r['resource'] not in leased:
                    needs_slot = True
                elif not r and control['creation_allowed'] and len(environments)<manager.config['dynamic']['max_environments']:
                    needs_slot = True
        rows = manager.db.execute("SELECT * FROM environments WHERE phase IN ('ready','stopping') AND running=1 AND resource NOT IN (SELECT resource FROM leases) ORDER BY last_used").fetchall()
        rows = [r for r in rows if r['phase']=='ready' or not process_alive(r['creator_pid'] or 0,r['creator_start'])]
        row = next((r for r in rows if force or r['phase']=='stopping' or
                    (r['resource'] not in protected and
                     (running>control['capacity'] or needs_slot or control.get('pressure')=='critical' or
                      now-r['last_used']>=manager.config['dynamic']['idle_shutdown_seconds']))),None)
        if not row:
            return None
        from .core import process_stamp
        manager.db.execute("UPDATE environments SET phase='stopping',creator_pid=?,creator_start=? WHERE resource=?",(os.getpid(),process_stamp(os.getpid()),row['resource']))
    spec = json.loads(row['spec'])
    try:
        stop_managed(manager,spec)
    except BaseException as e:
        with manager.transaction():
            manager.db.execute("UPDATE environments SET phase='ready',creator_pid=NULL,creator_start=NULL WHERE resource=?",(row['resource'],))
            manager.event('idle-stop-refused',row['resource'],row['session'],str(e))
        if isinstance(e,(KeyboardInterrupt,SystemExit)):
            raise
        return {'resource_id':row['resource'],'stopped':False,'reason':str(e)}
    with manager.transaction():
        manager.db.execute("UPDATE environments SET phase='ready',running=0,creator_pid=NULL,creator_start=NULL WHERE resource=?",(row['resource'],))
        manager.db.execute('DELETE FROM runtimes WHERE resource=?',(row['resource'],))
        manager.event('idle-stopped',row['resource'],row['session'])
    return {'resource_id':row['resource'],'stopped':True}
