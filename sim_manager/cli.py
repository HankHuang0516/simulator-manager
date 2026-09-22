import argparse
import json
import os
from pathlib import Path
import shlex
import signal
import sqlite3
import sys
from .core import Manager, ManagerError, positive
from .execution import execute, validate_runtime_command
from . import providers, __version__


def output(result, fmt):
    if fmt == 'json':
        print(json.dumps(result, ensure_ascii=False))
    elif fmt == 'shell':
        if 'token' in result:
            r = result['resource']
            values = {'SIM_MANAGER_TOKEN':result['token'], 'SIM_MANAGER_RESOURCE_ID':result['resource_id'],
                      'SIM_MANAGER_POOL':result['pool'], 'SIM_MANAGER_SESSION':result['session'],
                      'SIM_MANAGER_UDID':r.get('udid',''),
                      'SIM_MANAGER_SERIAL':f'emulator-{r["port"]}' if r['kind']=='android' else '',
                      'SIM_MANAGER_AVD':r.get('avd',''),
                      'SIM_MANAGER_HARD_EXPIRES':result['hard_expires'],
                      'SIM_MANAGER_EXPIRES':result['expires'],'SIM_MANAGER_YIELD_BY':result['yield_by'] or '',
                      'SIM_MANAGER_MODE':result['mode']}
            for key, value in values.items():
                print(f'export {key}={shlex.quote(str(value))}')
        else:
            print('SIM_MANAGER_RESULT=' + shlex.quote(json.dumps(result, ensure_ascii=False)))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


def parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--state-dir', default=argparse.SUPPRESS)
    common.add_argument('--config', default=argparse.SUPPRESS)
    common.add_argument('--json', dest='format', action='store_const', const='json', default=argparse.SUPPRESS)
    common.add_argument('--shell', dest='format', action='store_const', const='shell', default=argparse.SUPPRESS)
    p = argparse.ArgumentParser(description='Shared macOS FIFO resource scheduler', parents=[common])
    p.add_argument('--version',action='version',version='sim-manager '+__version__)
    subs = p.add_subparsers(dest='action', required=True)
    for name in ('acquire','run'):
        s = subs.add_parser(name, parents=[common])
        s.add_argument('pool')
        s.add_argument('--session')
        s.add_argument('--project')
        s.add_argument('--mode',choices=['auto','dynamic','traditional'],default='auto')
        s.add_argument('--budget-seconds',type=float)
        s.add_argument('--foreground',action='store_true',help='Serialize use of the shared desktop GUI')
        s.add_argument('--timeout', type=float, default=300, help='Queue wait seconds; 0 means try once')
        if name == 'acquire':
            s.add_argument('--owner-pid', type=int, help='Long-lived session/shell PID; default parent PID')
            s.add_argument('--lease-seconds', type=float)
        else:
            s.add_argument('--requeue-on-yield',action='store_true',help='Only for checkpointed/restartable commands')
            s.add_argument('--max-requeues',type=int,default=3)
            s.add_argument('--boot', action='store_true')
            s.add_argument('--boot-timeout', type=float, default=180)
            s.add_argument('--command-timeout', type=float, default=2400)
            # Commands must follow --. Parse separately so options before -- work.
    for name in ('release','renew','boot','repair-android-target'):
        s = subs.add_parser(name, parents=[common])
        s.add_argument('token', nargs='?', default=os.environ.get('SIM_MANAGER_TOKEN'))
        if name == 'renew':
            s.add_argument('--lease-seconds', type=float)
        if name == 'boot':
            s.add_argument('--timeout', type=float, default=180)
    s = subs.add_parser('enable',parents=[common])
    s.add_argument('--session')
    s.add_argument('--project')
    s.add_argument('--owner-pid',type=int)
    s.add_argument('--prepare',action='store_true',help='Create dedicated shutdown devices from installed SDK components')
    s = subs.add_parser('setup',parents=[common])
    s.add_argument('kind',choices=['ios','android','all'],default='all',nargs='?')
    for name in ('status','cleanup','validate-config'):
        subs.add_parser(name, parents=[common])
    s = subs.add_parser('audit',parents=[common],help='Detect unmanaged simulator commands and return session-specific coaching')
    s.add_argument('--session')
    s.add_argument('--acknowledge',action='store_true')
    s = subs.add_parser('ui',parents=[common],help='Open the native macOS floating dashboard')
    s.add_argument('--build-only',action='store_true',help='Build the local app without opening it')
    s.add_argument('--install-app',action='store_true',help='Install a Finder/Launchpad entry in ~/Applications')
    s.add_argument('--onboarding',action='store_true',help='Open the step-by-step Quick Start guide')
    s = subs.add_parser('watch',parents=[common])
    s.add_argument('--once',action='store_true')
    s.add_argument('--stop',action='store_true')
    s = subs.add_parser('_create',parents=[common],help=argparse.SUPPRESS)
    s.add_argument('token')
    s = subs.add_parser('discover', parents=[common])
    s.add_argument('kind', choices=['ios','android'])
    # Used only by gated provider workers, never invokes shutdown.
    s = subs.add_parser('_provider', parents=[common], help=argparse.SUPPRESS)
    s.add_argument('token')
    s.add_argument('--timeout', type=float, required=True)
    return p


def boot_command(m, token, timeout, fmt):
    return execute(m, token, [sys.executable, '-m', 'sim_manager', '_provider', token,
                             '--state-dir',str(m.state_dir),'--config',str(m.config_path.resolve()),
                             '--timeout',str(timeout)], timeout=timeout+5, operation='boot',
                   stdout=sys.stderr, stderr=sys.stderr)


def main(argv=None):
    os.umask(0o077)
    # Child workers must find this installation even from unrelated projects.
    root = str(Path(__file__).resolve().parent.parent)
    os.environ['PYTHONPATH'] = root + (os.pathsep+os.environ['PYTHONPATH'] if os.environ.get('PYTHONPATH') else '')
    argv = list(sys.argv[1:] if argv is None else argv)
    command = []
    if '--' in argv:
        at = argv.index('--')
        argv, command = argv[:at], argv[at+1:]
    p = parser()
    a = p.parse_args(argv)
    for key, value in [('format','text'),('state_dir',None),('config',None)]:
        if not hasattr(a,key):
            setattr(a,key,value)
    if a.action == 'run' and not command:
        p.error('run requires -- COMMAND [ARG ...]')
    if a.action != 'run' and command:
        p.error('Only run accepts a command after --')
    m = None
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    try:
        if a.action == 'run':
            validate_runtime_command(command)
        if a.action == 'ui':
            from .dashboard import launch
            output(launch(a.state_dir,a.build_only,install_app=a.install_app,onboarding=a.onboarding),a.format)
            return 0
        m = Manager(a.state_dir, a.config, allow_saved_config=a.action in ('release','renew','status','cleanup','boot','repair-android-target','_provider','_create','watch'))
        if a.action == 'enable':
            result = m.enable(a.session,a.project,a.owner_pid)
            if a.prepare:
                from .provision import prepare
                result['platforms'] = prepare(m)
                from .watchdog import ensure
                result['watcher'] = ensure(m)
        elif a.action == 'setup':
            from .provision import prepare
            result = {'platforms':prepare(m, ('ios','android') if a.kind=='all' else (a.kind,))}
        elif a.action == 'acquire':
            result = m.acquire(a.pool,a.session,a.project,a.owner_pid,a.timeout,a.lease_seconds,a.mode,a.budget_seconds,a.foreground)
        elif a.action == 'run':
            positive(a.command_timeout, 'command-timeout')
            positive(a.boot_timeout, 'boot-timeout')
            if a.max_requeues<0:
                raise ManagerError('max-requeues must be nonnegative')
            from .watchdog import ensure
            if m.config['mode']=='dynamic':
                ensure(m)
            attempts = 0
            while True:
                lease = m.acquire(a.pool,a.session,a.project,os.getpid(),a.timeout,None,a.mode,a.budget_seconds,a.foreground)
                token = lease['token']
                rc = 1
                try:
                    rc = boot_command(m,token,a.boot_timeout,a.format) if a.boot else 0
                    if rc==0:
                        r = lease['resource']
                        env = {**os.environ,'SIM_MANAGER_TOKEN':token,
                               'SIM_MANAGER_RESOURCE_ID':r['id'],'SIM_MANAGER_POOL':a.pool,
                               'SIM_MANAGER_SESSION':lease['session'],'SIM_MANAGER_UDID':r.get('udid',''),
                               'SIM_MANAGER_SERIAL':f'emulator-{r["port"]}' if r['kind']=='android' else '',
                               'SIM_MANAGER_AVD':r.get('avd',''),'SIM_MANAGER_HARD_EXPIRES':str(lease['hard_expires'])}
                        rc = execute(m,token,command,env,a.command_timeout,
                                     stdout=sys.stderr if a.format=='json' else None)
                finally:
                    m.release(token)
                if rc!=75 or not a.requeue_on_yield or attempts>=a.max_requeues:
                    break
                attempts += 1
                print('sim-manager: yielded safely; rejoining the FIFO queue',file=sys.stderr)
            if a.format=='json':
                output({'resource_id':lease['resource_id'],'exit_code':rc,'released':True,
                        'requeues':attempts,'requeue_required':rc==75,'mode':lease['mode']},a.format)
            return rc
        elif a.action == '_create':
            from .dynamic import provision_environment
            provision_environment(m,a.token)
            return 0
        elif a.action == 'watch':
            from .watchdog import watch,stop
            result = stop(m) if a.stop else watch(m,a.once)
        elif a.action in ('release','renew','boot','repair-android-target','_provider'):
            if not a.token:
                raise ManagerError('Pass a lease token or set SIM_MANAGER_TOKEN')
            if a.action == 'release':
                result = m.release(a.token)
            elif a.action == 'renew':
                result = m.renew(a.token, a.lease_seconds)
            elif a.action == 'repair-android-target':
                from .provision import repair_leased_android_target
                result = repair_leased_android_target(m,a.token)
            elif a.action == '_provider':
                r = m.get_lease(a.token)
                providers.boot(m, json.loads(r['spec']), a.timeout)
                return 0
            else:
                positive(a.timeout, 'boot timeout')
                # Refuse a token already used by another active operation.
                rc = 1
                try:
                    rc = boot_command(m, a.token, a.timeout, a.format)
                finally:
                    if rc:
                        m.release(a.token)
                if rc:
                    output({'booted':False,'error':'Provider boot failed; see stderr','code':rc}, a.format)
                    return rc
                result = {'booted':True, 'resource_id':m.get_lease(a.token)['resource']}
        elif a.action == 'status':
            result = m.status()
        elif a.action == 'cleanup':
            result = m.cleanup()
        elif a.action == 'audit':
            from . import compliance
            if a.acknowledge:
                if not a.session:
                    raise ManagerError('--acknowledge requires --session')
                compliance.acknowledge(m,a.session)
            result = compliance.audit(m,a.session)
        elif a.action == 'validate-config':
            result = {'valid':True,'config':str(m.config_path)}
        else:
            result = providers.discover(m, a.kind)
        output(result, a.format)
        return 0
    except KeyboardInterrupt:
        if a.format == 'json':
            output({'error':'Interrupted','code':130}, 'json')
        else:
            print('sim-manager: interrupted', file=sys.stderr)
        return 130
    except (ManagerError, OSError, ValueError, sqlite3.Error) as e:
        code = getattr(e, 'code', 1)
        if a.format == 'json':
            output({'error':str(e),'code':code}, 'json')
        else:
            print(f'sim-manager: {e}', file=sys.stderr)
        return code
    finally:
        if m:
            m.close()
