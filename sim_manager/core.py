"""Transactional, cooperative resource scheduler. Python 3.9+, stdlib only."""
import contextlib
import json
import math
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import time


class ManagerError(Exception):
    code = 1


class WaitTimeout(ManagerError):
    code = 3


class OwnershipError(ManagerError):
    code = 4


def boot_id():
    if sys.platform == 'darwin':
        return subprocess.check_output(['/usr/sbin/sysctl', '-n', 'kern.boottime'], text=True).strip()
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def process_stamp(pid):
    if pid < 2:
        return None
    p = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'lstart=', '-o', 'stat='],
                       capture_output=True, text=True, env={**os.environ, 'LC_ALL': 'C'})
    parts = p.stdout.strip().split()
    if len(parts) < 6 or parts[-1].startswith('Z'):
        return None
    return ' '.join(parts[:-1])


def process_alive(pid, stamp):
    return bool(stamp) and process_stamp(pid) == stamp


def group_alive(pgid, machine_boot):
    if not pgid or machine_boot != boot_id():
        return False
    p = subprocess.run(['/bin/ps', '-axo', 'pgid=,stat='], capture_output=True, text=True)
    if p.returncode:
        raise ManagerError('Cannot inspect process groups; refusing to recycle resources')
    return any(len(v) == 2 and v[0] == str(pgid) and not v[1].startswith('Z')
               for v in (line.split() for line in p.stdout.splitlines()))


def positive(value, name, allow_zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or (not allow_zero and value == 0):
        raise ManagerError(f'{name} must be a finite {"nonnegative" if allow_zero else "positive"} number')
    return value


def load_config(path):
    try:
        c = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        raise ManagerError(f'Cannot read configuration {path}: {e}') from e
    if not isinstance(c, dict) or c.get('version') != 1 or not isinstance(c.get('pools'), dict):
        raise ManagerError('Configuration requires version: 1 and pools object')
    if 'tools' in c and (not isinstance(c['tools'],dict) or any(k not in ('xcrun','adb','emulator','avdmanager') or not isinstance(v,str) or not v for k,v in c['tools'].items())):
        raise ManagerError('tools must map xcrun/adb/emulator/avdmanager names to nonempty executable paths')
    if 'android_sdk' in c and (not isinstance(c['android_sdk'],str) or not c['android_sdk']):
        raise ManagerError('android_sdk must be a nonempty SDK path')
    c.setdefault('lease_seconds', 900)
    c.setdefault('poll_seconds', .25)
    c.setdefault('global_capacity', 2)
    positive(c['lease_seconds'], 'lease_seconds')
    positive(c['poll_seconds'], 'poll_seconds')
    positive(c['global_capacity'], 'global_capacity')
    seen_ids, seen_devices, seen_avds, seen_ports = set(), set(), set(), set()
    for name, pool in c['pools'].items():
        if not name or not isinstance(pool, dict):
            raise ManagerError('Invalid pool')
        pool.setdefault('capacity', 1)
        positive(pool['capacity'], f'{name}.capacity', True)
        if not isinstance(pool['capacity'],int):
            raise ManagerError('Pool capacity must be a nonnegative integer')
        if not isinstance(pool.get('resources'), list):
            raise ManagerError(f'{name}.resources must be an array')
        for r in pool['resources']:
            if not isinstance(r, dict) or not isinstance(r.get('id'), str) or not r['id'] or r['id'] in seen_ids:
                raise ManagerError('Resource IDs must be nonempty and globally unique')
            seen_ids.add(r['id'])
            r.setdefault('enabled', True)
            r.setdefault('cost', 1)
            r.setdefault('allow_attach', False)
            if not isinstance(r['enabled'], bool) or not isinstance(r['allow_attach'], bool):
                raise ManagerError('enabled/allow_attach must be booleans')
            positive(r['cost'], 'resource cost')
            if r['cost'] > c['global_capacity']:
                raise ManagerError('Resource cost exceeds global_capacity')
            kind = r.get('kind')
            if kind not in ('ios', 'android', 'generic'):
                raise ManagerError('kind must be ios, android, or generic')
            if kind == 'ios':
                if not isinstance(r.get('udid'), str) or not r['udid']:
                    raise ManagerError('iOS resource requires udid')
                if r['udid'].upper() in seen_devices:
                    raise ManagerError('Duplicate iOS UDID across pools')
                seen_devices.add(r['udid'].upper())
            if kind == 'android':
                port = r.get('port')
                if not isinstance(port, int) or isinstance(port, bool) or port % 2 or not 5554 <= port <= 5682:
                    raise ManagerError('Android port must be even, 5554..5682')
                if not isinstance(r.get('avd'), str) or not r['avd']:
                    raise ManagerError('Android resource requires avd')
                if r['avd'] in seen_avds or port in seen_ports:
                    raise ManagerError('Each Android AVD and port must be unique across pools')
                seen_avds.add(r['avd'])
                seen_ports.add(port)
                if 'avd_home' in r and (not isinstance(r['avd_home'],str) or not r['avd_home']):
                    raise ManagerError('avd_home must be a nonempty path')
                if 'args' in r:
                    raise ManagerError('Android extra args are not supported; ports/AVD must stay pinned')
    return c


class Manager:
    def __init__(self, state_dir=None, config_path=None, allow_saved_config=False):
        self.state_dir = Path(state_dir or os.environ.get('SIM_MANAGER_STATE_DIR') or
                              Path.home() / 'Library/Application Support/simulator-manager').expanduser().resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.state_dir.stat().st_uid != os.getuid():
            raise ManagerError('State directory must belong to this macOS user')
        os.chmod(self.state_dir, 0o700)
        self.config_path = Path(config_path or os.environ.get('SIM_MANAGER_CONFIG') or self.state_dir / 'config.json').expanduser()
        self.config_error = None
        try:
            self.requested = load_config(self.config_path)
        except ManagerError as e:
            if not allow_saved_config:
                raise
            self.requested = None
            self.config_error = str(e)
        self.machine_boot = boot_id()
        self.db = sqlite3.connect(self.state_dir / 'state.sqlite3', timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA busy_timeout=10000')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS queue(
          seq INTEGER PRIMARY KEY AUTOINCREMENT, request TEXT UNIQUE NOT NULL,
          pool TEXT NOT NULL, session TEXT NOT NULL, project TEXT NOT NULL,
          owner_pid INTEGER NOT NULL, owner_start TEXT NOT NULL, boot TEXT NOT NULL,
          waiter_pid INTEGER NOT NULL, waiter_start TEXT NOT NULL, deadline REAL NOT NULL,
          ttl REAL NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS leases(
          resource TEXT PRIMARY KEY, token TEXT UNIQUE NOT NULL, pool TEXT NOT NULL,
          session TEXT NOT NULL, project TEXT NOT NULL, owner_pid INTEGER NOT NULL,
          owner_start TEXT NOT NULL, boot TEXT NOT NULL, expires REAL NOT NULL,
          created REAL NOT NULL, spec TEXT NOT NULL,
          activity_pid INTEGER, activity_start TEXT, activity_group INTEGER,
          operation TEXT);
        CREATE TABLE IF NOT EXISTS runtimes(resource TEXT PRIMARY KEY,spec TEXT NOT NULL,
          boot TEXT NOT NULL, pid INTEGER, start TEXT, phase TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(session TEXT PRIMARY KEY, project TEXT NOT NULL,
          enabled_at REAL NOT NULL,last_seen REAL NOT NULL,boot TEXT NOT NULL,
          owner_pid INTEGER,owner_start TEXT);
        CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,
          time REAL NOT NULL,event TEXT NOT NULL,resource TEXT,session TEXT,detail TEXT);
        ''')
        with self.transaction():
            self.initial_reaped = self.sweep()
            row = self.db.execute("SELECT value FROM meta WHERE key='config'").fetchone()
            if self.requested is None and not row:
                raise ManagerError(self.config_error)
            if self.requested is not None and (not row or (not self.db.execute('SELECT 1 FROM leases UNION ALL SELECT 1 FROM queue LIMIT 1').fetchone())):
                self.config = self.requested
                self.db.execute("INSERT OR REPLACE INTO meta VALUES('config',?)", (self.canonical(self.config),))
            else:
                self.config = json.loads(row['value'])
        os.chmod(self.state_dir / 'state.sqlite3', 0o600)

    @staticmethod
    def canonical(c):
        return json.dumps(c, sort_keys=True, separators=(',', ':'))

    @contextlib.contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.execute('COMMIT')
        except BaseException:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            raise

    def close(self):
        self.db.close()

    def event(self, kind, resource=None, session=None, detail=None):
        self.db.execute('INSERT INTO events(time,event,resource,session,detail) VALUES(?,?,?,?,?)',
                        (time.time(), kind, resource, session, detail))
        self.db.execute('DELETE FROM events WHERE seq < (SELECT COALESCE(MAX(seq),0)-1000 FROM events)')

    def owner_alive(self, r):
        return r['boot'] == self.machine_boot and process_alive(r['owner_pid'], r['owner_start'])

    def activity_alive(self, r):
        if r['boot'] != self.machine_boot:
            return False
        return (group_alive(r['activity_group'], r['boot']) if r['activity_group'] else
                process_alive(r['activity_pid'] or 0, r['activity_start']))

    def sweep(self):
        """Inside transaction. Never recycle a live workload merely due to TTL."""
        now = time.time()
        for r in self.db.execute('SELECT * FROM queue').fetchall():
            if r['deadline'] < now or not self.owner_alive(r) or not process_alive(r['waiter_pid'], r['waiter_start']):
                self.db.execute('DELETE FROM queue WHERE request=?', (r['request'],))
                self.event('queue-reaped', session=r['session'])
        reaped = []
        for r in self.db.execute('SELECT * FROM leases').fetchall():
            busy = self.activity_alive(r)
            if not self.owner_alive(r) and not busy:
                self.db.execute('DELETE FROM leases WHERE token=?', (r['token'],))
                self.event('stale-reaped', r['resource'], r['session'])
                reaped.append(r['resource'])
            elif r['activity_pid'] and not busy:
                self.db.execute('UPDATE leases SET activity_pid=NULL,activity_start=NULL,activity_group=NULL,operation=NULL WHERE token=?', (r['token'],))
        return reaped

    def candidates(self, pool, leases):
        p = self.config['pools'][pool]
        if sum(r['pool'] == pool for r in leases) >= p['capacity']:
            return []
        used = {r['resource'] for r in leases}
        cost = sum(json.loads(r['spec'])['cost'] for r in leases)
        return [r for r in p['resources'] if r['enabled'] and r['id'] not in used
                and cost + r['cost'] <= self.config['global_capacity']]

    def acquire(self, pool, session=None, project=None, owner_pid=None, timeout=300, ttl=None):
        positive(timeout, 'timeout', True)
        ttl = positive(ttl if ttl is not None else self.config['lease_seconds'], 'lease seconds')
        if self.canonical(self.requested) != self.canonical(self.config):
            raise ManagerError('Config changed while resources are busy; release/drain before acquiring with new config')
        if pool not in self.config['pools']:
            raise ManagerError(f'Unknown pool: {pool}')
        p = self.config['pools'][pool]
        if not p['capacity'] or not any(r['enabled'] for r in p['resources']):
            raise ManagerError(f'Pool {pool} has no enabled capacity; configure dedicated devices first')
        pid = owner_pid if owner_pid is not None else os.getppid()
        start = process_stamp(pid)
        waiter = process_stamp(os.getpid())
        if not start or not waiter:
            raise ManagerError('owner-pid must identify a live non-zombie process (PID >= 2)')
        session = session or os.environ.get('SIM_MANAGER_SESSION') or os.environ.get('CODEX_THREAD_ID') or f'pid-{pid}'
        project = project or str(Path.cwd())
        request = secrets.token_hex(16)
        end = time.monotonic() + timeout
        # The first allocation attempt also works with timeout=0.
        with self.transaction():
            self.sweep()
            current = self.db.execute("SELECT value FROM meta WHERE key='config'").fetchone()
            if current['value'] != self.canonical(self.requested):
                raise ManagerError('Shared configuration changed; retry acquire with current configuration')
            self.db.execute('INSERT INTO queue(request,pool,session,project,owner_pid,owner_start,boot,waiter_pid,waiter_start,deadline,ttl,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                            (request, pool, session, project, pid, start, self.machine_boot, os.getpid(), waiter, time.time()+max(timeout, .1), ttl, time.time()))
            self.db.execute('UPDATE sessions SET last_seen=? WHERE session=?',(time.time(),session))
            self.event('queued', session=session, detail=pool)
        try:
            first = True
            while True:
                with self.transaction():
                    self.sweep()
                    if not self.db.execute('SELECT 1 FROM queue WHERE request=?', (request,)).fetchone():
                        raise WaitTimeout('Request expired or owner exited')
                    leases = self.db.execute('SELECT * FROM leases').fetchall()
                    heads = self.db.execute('SELECT * FROM queue WHERE seq IN (SELECT MIN(seq) FROM queue GROUP BY pool) ORDER BY seq').fetchall()
                    eligible = next((h for h in heads if self.candidates(h['pool'], leases)), None)
                    if eligible and eligible['request'] == request:
                        if not first and time.monotonic() >= end:
                            raise WaitTimeout(f'Timed out waiting for {pool}')
                        r = self.candidates(pool, leases)[0]
                        token = secrets.token_urlsafe(32)
                        now = time.time()
                        self.db.execute('INSERT INTO leases(resource,token,pool,session,project,owner_pid,owner_start,boot,expires,created,spec) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                                        (r['id'], token, pool, session, project, pid, start, self.machine_boot, now+ttl, now, json.dumps(r)))
                        self.db.execute('DELETE FROM queue WHERE request=?', (request,))
                        self.event('acquired', r['id'], session)
                        return self.lease_result(self.db.execute('SELECT * FROM leases WHERE token=?', (token,)).fetchone(), True)
                first = False
                if time.monotonic() >= end:
                    raise WaitTimeout(f'Timed out waiting for {pool}')
                time.sleep(min(self.config['poll_seconds'], max(.001, end-time.monotonic())))
        finally:
            with self.transaction():
                self.db.execute('DELETE FROM queue WHERE request=?', (request,))

    def get_lease(self, token, allow_expired=False):
        r = self.db.execute('SELECT * FROM leases WHERE token=?', (token,)).fetchone()
        if not r:
            raise OwnershipError('Unknown or reclaimed lease token')
        if not allow_expired and (r['expires'] < time.time() or not self.owner_alive(r)):
            raise OwnershipError('Lease expired or owner exited; renew with a live owner or release')
        return r

    def renew(self, token, ttl=None):
        ttl = positive(ttl if ttl is not None else self.config['lease_seconds'], 'lease seconds')
        with self.transaction():
            self.sweep()
            r = self.get_lease(token, True)
            if not self.owner_alive(r):
                raise OwnershipError('Cannot renew a dead owner')
            self.db.execute('UPDATE leases SET expires=? WHERE token=?', (time.time()+ttl, token))
            return self.lease_result(self.db.execute('SELECT * FROM leases WHERE token=?', (token,)).fetchone(), True)

    def release(self, token):
        with self.transaction():
            r = self.db.execute('SELECT * FROM leases WHERE token=?', (token,)).fetchone()
            if not r:
                # Retry-safe, but never release by resource ID or session name.
                return {'released': False, 'reason': 'unknown-or-already-released'}
            if self.activity_alive(r):
                raise OwnershipError('Work is still active; finish/cancel your work before release')
            self.db.execute('DELETE FROM leases WHERE token=?', (token,))
            self.event('released', r['resource'], r['session'])
            return {'released': True, 'resource_id': r['resource']}

    def begin_activity(self, token, operation, pid=None, group=None):
        pid = pid or os.getpid()
        stamp = process_stamp(pid)
        if not stamp:
            raise OwnershipError('Activity process exited before registration')
        with self.transaction():
            self.sweep()
            r = self.get_lease(token)
            if self.activity_alive(r):
                raise OwnershipError('Lease already has active work')
            self.db.execute('UPDATE leases SET activity_pid=?,activity_start=?,activity_group=?,operation=? WHERE token=?', (pid, stamp, group, operation, token))
            return dict(json.loads(r['spec']))

    def end_activity(self, token):
        with self.transaction():
            self.db.execute('UPDATE leases SET activity_pid=NULL,activity_start=NULL,activity_group=NULL,operation=NULL WHERE token=?', (token,))

    def runtime(self, resource):
        r = self.db.execute('SELECT * FROM runtimes WHERE resource=?', (resource['id'],)).fetchone()
        return dict(r) if r and r['spec'] == self.canonical(resource) and r['boot'] == self.machine_boot else None

    def mark_runtime(self, resource, phase, pid=None):
        with self.transaction():
            self.db.execute('INSERT OR REPLACE INTO runtimes VALUES(?,?,?,?,?,?)',
                            (resource['id'], self.canonical(resource), self.machine_boot, pid,
                             process_stamp(pid) if pid else None, phase))

    def lease_result(self, r, include_token=False):
        out = {k: r[k] for k in ('pool','session','project','owner_pid','owner_start','created','expires','operation')}
        out.update(resource_id=r['resource'], resource=json.loads(r['spec']))
        out['expired'] = r['expires'] < time.time()
        if include_token:
            out['token'] = r['token']
        return out

    def enable(self, session=None, project=None, owner_pid=None):
        session = session or os.environ.get('SIM_MANAGER_SESSION') or os.environ.get('CODEX_THREAD_ID') or 'session-'+secrets.token_hex(8)
        project = project or str(Path.cwd())
        start = process_stamp(owner_pid) if owner_pid else None
        if owner_pid and not start:
            raise ManagerError('Session owner-pid must identify a live process')
        with self.transaction():
            now = time.time()
            self.db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?) ON CONFLICT(session) DO UPDATE SET project=excluded.project,last_seen=excluded.last_seen,boot=excluded.boot,owner_pid=excluded.owner_pid,owner_start=excluded.owner_start',
                            (session,project,now,now,self.machine_boot,owner_pid,start))
            self.event('session-enabled',session=session)
        return {'shared_mode':True,'session':session,'project':project,'state_dir':str(self.state_dir),
                'coordination':'cooperative','next':'Use sim-manager run with this session label for every runtime/UI test'}

    def status(self):
        with self.transaction():
            reaped = self.initial_reaped + self.sweep()
            self.initial_reaped = []
            leases = [self.lease_result(r) for r in self.db.execute('SELECT * FROM leases ORDER BY created')]
            queue = [dict(r) for r in self.db.execute('SELECT seq,pool,session,project,owner_pid,created,deadline FROM queue ORDER BY seq')]
            return {'state_dir':str(self.state_dir), 'config_error':self.config_error, 'config_pending': self.canonical(self.config)!=self.canonical(self.requested),
                    'pools':self.config['pools'], 'global_capacity':self.config['global_capacity'],
                    'leases':leases, 'queue':queue, 'reaped':reaped,
                    'sessions':[dict(r) for r in self.db.execute('SELECT * FROM sessions ORDER BY last_seen DESC')],
                    'events':[dict(r) for r in self.db.execute('SELECT * FROM events ORDER BY seq DESC LIMIT 30')]}

    def cleanup(self):
        with self.transaction():
            reaped = self.initial_reaped + self.sweep()
            self.initial_reaped = []
            return {'reaped':reaped, 'remaining_leases':self.db.execute('SELECT COUNT(*) FROM leases').fetchone()[0],
                    'remaining_queue':self.db.execute('SELECT COUNT(*) FROM queue').fetchone()[0]}
