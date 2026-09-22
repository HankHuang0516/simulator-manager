"""Read-only compliance coaching for registered Codex sessions.

The watcher observes short-lived simulator commands in registered session process
trees. It records guidance but never signals a task, simulator, emulator, or adb.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import time

from .core import boot_matches, process_alive


GUIDANCE = {
    'ios': (
        'This task used an iOS simulator outside Simulator Manager. Build and run host '
        'tests first. For runtime or UI validation, use simulator_manager_run (or '
        'sim-manager run ios) with this task ID and project path; target only the '
        'returned SIM_MANAGER_UDID and let the supervised call release automatically.'
    ),
    'android': (
        'This task used an Android emulator outside Simulator Manager. Build and run '
        'host tests first. For runtime or UI validation, use simulator_manager_run '
        '(or sim-manager run android) with this task ID and project path; target only '
        'the returned SIM_MANAGER_SERIAL and never stop the shared adb server.'
    ),
}


def _processes():
    """Return pid -> (ppid, command). Commands are never persisted verbatim."""
    try:
        result = subprocess.run(['/bin/ps', '-axo', 'pid=,ppid=,command='],
                                capture_output=True, text=True, timeout=5, check=True)
    except (OSError, subprocess.SubprocessError):
        return {}
    rows = {}
    for line in result.stdout.splitlines():
        match = re.match(r'\s*(\d+)\s+(\d+)\s+(.*)$', line)
        if match:
            rows[int(match.group(1))] = (int(match.group(2)), match.group(3))
    return rows


def _descendants(processes, root):
    children = {}
    for pid, (ppid, _command) in processes.items():
        children.setdefault(ppid, []).append(pid)
    found, pending = set(), list(children.get(root, ()))
    while pending:
        pid = pending.pop()
        if pid in found:
            continue
        found.add(pid)
        pending.extend(children.get(pid, ()))
    return found


def _classify(command):
    lower = command.lower()
    # Manager-supervised work is checked through the lease table before findings
    # are recorded. Ignore the manager's own inspection and provider processes.
    if 'sim-manager' in lower or '-m sim_manager' in lower:
        return None
    if re.search(r'(^|[/\s])xcodebuild([\s]|$)', lower) and (
            'destination' in lower and ('simulator' in lower or 'id=' in lower)):
        return 'ios', 'xcodebuild-simulator'
    if 'simctl' in lower and re.search(
            r'\b(boot|launch|install|io|spawn|openurl|terminate|shutdown|erase)\b', lower):
        return 'ios', 'direct-simctl'
    if re.search(r'(^|[/\s])emulator([\s]|$)', lower) and '-list-avds' not in lower:
        return 'android', 'direct-emulator'
    if re.search(r'(^|[/\s])adb([\s]|$)', lower):
        harmless = (' devices', ' version', ' help', ' start-server')
        if not any(value in lower for value in harmless):
            return 'android', 'direct-adb'
    return None


def _fingerprint(session, platform, kind):
    value = '\0'.join((session, platform, kind)).encode()
    return hashlib.sha256(value).hexdigest()[:24]


def audit(manager, session=None):
    """Observe registered process trees and persist bounded coaching findings."""
    now, processes, detected = time.time(), _processes(), {}
    sessions = manager.db.execute('SELECT * FROM sessions' +
                                  (' WHERE session=?' if session else ''),
                                  (session,) if session else ()).fetchall()
    leases = {(row['session'], row['pool']) for row in manager.db.execute('SELECT session,pool FROM leases')}
    observable = 0
    for row in sessions:
        owner = row['owner_pid']
        if not owner or not boot_matches(row['boot'], manager.machine_boot) or not process_alive(owner, row['owner_start']):
            continue
        observable += 1
        for pid in _descendants(processes, owner):
            classified = _classify(processes.get(pid, (0, ''))[1])
            if not classified:
                continue
            platform, kind = classified
            if (row['session'], platform) in leases:
                continue
            key = _fingerprint(row['session'], platform, kind)
            detected[key] = (row, platform, kind, pid)
    with manager.transaction():
        for fingerprint, (row, platform, kind, pid) in detected.items():
            existing = manager.db.execute(
                'SELECT active FROM compliance_findings WHERE fingerprint=?', (fingerprint,)).fetchone()
            manager.db.execute('''INSERT INTO compliance_findings
                (fingerprint,session,project,platform,kind,pid,first_seen,last_seen,active,guidance)
                VALUES(?,?,?,?,?,?,?,?,1,?)
                ON CONFLICT(fingerprint) DO UPDATE SET pid=excluded.pid,last_seen=excluded.last_seen,
                    active=1,guidance=excluded.guidance''',
                (fingerprint, row['session'], row['project'], platform, kind, pid, now, now, GUIDANCE[platform]))
            if not existing or not existing['active']:
                manager.event('compliance-guidance', session=row['session'], detail=kind)
        active = manager.db.execute('SELECT fingerprint FROM compliance_findings WHERE active=1').fetchall()
        for row in active:
            if row['fingerprint'] not in detected:
                manager.db.execute('UPDATE compliance_findings SET active=0,last_seen=? WHERE fingerprint=?',
                                   (now, row['fingerprint']))
        manager.db.execute('DELETE FROM compliance_findings WHERE last_seen<?', (now-30*86400,))
    query = '''SELECT fingerprint,session,project,platform,kind,pid,first_seen,last_seen,
                      active,guidance,acknowledged_at
               FROM compliance_findings'''
    values = []
    if session:
        query += ' WHERE session=?'
        values.append(session)
    query += ' ORDER BY active DESC,last_seen DESC LIMIT 50'
    findings = [dict(row) for row in manager.db.execute(query, values)]
    return {
        'observed_at': now,
        'registered_sessions': len(sessions),
        'observable_sessions': observable,
        'active_findings': sum(1 for row in findings if row['active']),
        'findings': findings,
        'enforcement': 'guidance-only',
        'safety': 'No process, task, simulator, emulator, or adb server is stopped by compliance coaching.',
    }


def acknowledge(manager, session):
    now = time.time()
    with manager.transaction():
        manager.db.execute('UPDATE compliance_findings SET acknowledged_at=? WHERE session=? AND active=1',
                           (now, session))
    return {'session': session, 'acknowledged': True, 'time': now}


def recent(manager, active_only=True):
    where = ' WHERE active=1' if active_only else ''
    return [dict(row) for row in manager.db.execute(
        '''SELECT fingerprint,session,project,platform,kind,pid,first_seen,last_seen,
                  active,guidance,acknowledged_at FROM compliance_findings''' + where +
        ' ORDER BY active DESC,last_seen DESC LIMIT 50')]
