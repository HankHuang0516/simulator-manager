"""Host-pressure sampling and hysteretic admission control; no active VM eviction."""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

STAGES = ('dynamic', 'constrained', 'draining', 'traditional')


def sample(state_dir):
    result = {'time':time.time(), 'load_ratio':os.getloadavg()[0]/max(1, os.cpu_count() or 1),
              'disk_free_gib':shutil.disk_usage(state_dir).free/(1024**3)}
    try:
        if sys.platform == 'darwin':
            p = subprocess.run(['/usr/bin/memory_pressure','-Q'], capture_output=True, text=True, timeout=3)
            match = re.search(r'System-wide memory free percentage:\s*(\d+)%', p.stdout)
            if p.returncode or not match:
                raise ValueError('memory_pressure unavailable')
            result['memory_free_percent'] = float(match.group(1))
        else:
            values = {line.split(':')[0]:int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines() if len(line.split())>=2 and line.split()[1].isdigit()}
            result['memory_free_percent'] = 100*values['MemAvailable']/values['MemTotal']
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as e:
        result['error'] = str(e)
    return result


def advance(previous, metrics, settings):
    """Pure controller: one stage per sustained bad interval; slower recovery."""
    state = dict(previous or {'stage':0,'bad':0,'good':0})
    critical = ('error' in metrics or metrics.get('memory_free_percent',0)<=settings['critical_memory_percent']
                or metrics['load_ratio']>=settings['critical_load_ratio'] or metrics['disk_free_gib']<=settings['critical_disk_gib'])
    bad = critical or metrics.get('memory_free_percent',0)<=settings['low_memory_percent'] or metrics['load_ratio']>=settings['high_load_ratio'] or metrics['disk_free_gib']<=settings['low_disk_gib']
    healthy = (not bad and metrics.get('memory_free_percent',0)>=settings['recovery_memory_percent']
               and metrics['load_ratio']<=settings['recovery_load_ratio'] and metrics['disk_free_gib']>settings['low_disk_gib'])
    state['bad'] = state.get('bad',0)+1 if bad else 0
    state['good'] = state.get('good',0)+1 if healthy else 0
    if critical and state['stage']==0:
        state['stage'] = 1  # Immediately stop creating VMs on critical telemetry.
        state['bad'] = 0
    elif state['bad']>=settings['bad_samples']:
        state['stage'] = min(3,state['stage']+1)
        state['bad'] = 0
    elif state['good']>=settings['good_samples']:
        state['stage'] = max(0,state['stage']-1)
        state['good'] = 0
    state.update(sampled_at=metrics['time'], metrics=metrics, pressure='critical' if critical else 'elevated' if bad else 'healthy' if healthy else 'neutral')
    return state


def update(manager, force=False, metrics=None):
    settings = manager.config['monitor']
    row = manager.db.execute("SELECT value FROM meta WHERE key='controller'").fetchone()
    old = json.loads(row[0]) if row else {'stage':0,'bad':0,'good':0,'sampled_at':0}
    if metrics is None and not force and time.time()-old.get('sampled_at',0)<settings['sample_seconds']:
        return old
    values = metrics or sample(manager.state_dir)
    with manager.transaction():
        # Concurrent callers must not count one time interval several times.
        row = manager.db.execute("SELECT value FROM meta WHERE key='controller'").fetchone()
        old = json.loads(row[0]) if row else old
        if metrics is None and not force and values['time']-old.get('sampled_at',0)<settings['sample_seconds']:
            return old
        new = advance(old, values, settings)
        manager.db.execute("INSERT OR REPLACE INTO meta VALUES('controller',?)", (json.dumps(new),))
        if new['stage']!=old['stage']:
            manager.event('mode-transition',detail=STAGES[new['stage']])
        return new


def admission(manager):
    row = manager.db.execute("SELECT value FROM meta WHERE key='controller'").fetchone()
    state = json.loads(row[0]) if row else {'stage':0}
    stage = state['stage'] if manager.config['mode']=='dynamic' else 3
    maximum = manager.config['dynamic']['max_parallel']
    limit = maximum if stage<=1 else max(1,maximum//2) if stage==2 else manager.config['global_capacity']
    return {'stage':stage, 'mode':STAGES[stage], 'capacity':limit, 'creation_allowed':stage==0,
            'metrics':state.get('metrics'), 'pressure':state.get('pressure','unknown')}
