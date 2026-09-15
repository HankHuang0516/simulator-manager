"""Only explicit device IDs/serials. Never shutdown, erase, or kill a shared server."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
from .core import ManagerError, process_alive
from .execution import spawn_gated


def tool(manager, name):
    value = manager.config.get('tools', {}).get(name)
    if value:
        value = os.path.expanduser(value)
        if not os.access(value, os.X_OK):
            raise ManagerError(f'Tool is not executable: {value}')
        return value
    found = shutil.which(name)
    if found:
        return found
    if name in ('adb', 'emulator', 'avdmanager'):
        sdk = Path(os.environ.get('ANDROID_SDK_ROOT') or os.environ.get('ANDROID_HOME') or Path.home()/'Library/Android/sdk')
        sdk = Path(manager.config.get('android_sdk') or sdk).expanduser()
        if name == 'avdmanager':
            candidates = [sdk/'cmdline-tools/latest/bin/avdmanager', *sorted((sdk/'cmdline-tools').glob('*/bin/avdmanager'),reverse=True)]
            candidate = next((p for p in candidates if p.is_file() and os.access(p,os.X_OK)),candidates[0])
        else:
            candidate = sdk / ('platform-tools/adb' if name == 'adb' else 'emulator/emulator')
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise ManagerError(f'{name} unavailable; configure tools.{name} or install the SDK')


def call(args, timeout=30, env=None):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ManagerError(f'Tool failed: {args[0]}: {e}') from e
    if p.returncode:
        raise ManagerError(f'Command failed ({p.returncode}): {args!r}\n{p.stderr.strip()}')
    return p.stdout.strip()


def android_devices(adb):
    return {v[0]:v[1] for v in (line.split() for line in call([adb, 'devices']).splitlines()[1:]) if len(v)>=2}


def avd_name(adb, serial):
    lines = call([adb, '-s', serial, 'emu', 'avd', 'name'], 10).splitlines()
    return next((line.strip() for line in lines if line.strip() and line.strip() != 'OK'), '')


def ports_free(port):
    held = []
    try:
        for number in (port, port+1):
            s = socket.socket()
            held.append(s)
            s.bind(('127.0.0.1', number))
        return True
    except OSError:
        return False
    finally:
        for s in held:
            s.close()


def boot(manager, resource, timeout=180):
    runtime = manager.runtime(resource)
    kind = resource['kind']
    if kind == 'generic':
        return
    deadline = time.monotonic() + timeout
    if kind == 'ios':
        xcrun = tool(manager, 'xcrun')
        devices = json.loads(call([xcrun, 'simctl', 'list', 'devices', '--json']))['devices']
        device = next((d for ds in devices.values() for d in ds if d['udid'].upper() == resource['udid'].upper()), None)
        if not device or not device.get('isAvailable', False):
            raise ManagerError('Configured iOS device is missing or unavailable')
        if device['state'] == 'Booted':
            if not runtime and not resource['allow_attach']:
                raise ManagerError('iOS simulator is already booted outside this manager; refusing to attach')
        elif device['state'] == 'Shutdown':
            # Intent is durable before simctl's side effect. A killed boot worker
            # leaves a recoverable manager-owned boot, never an unknown attachment.
            manager.mark_runtime(resource, 'starting')
            call([xcrun, 'simctl', 'boot', resource['udid']], max(.1, deadline-time.monotonic()))
        else:
            if not runtime:
                raise ManagerError(f'iOS device transitioning outside manager: {device["state"]}')
        call([xcrun, 'simctl', 'bootstatus', resource['udid']], max(.1, deadline-time.monotonic()))
        manager.mark_runtime(resource, 'ready')
        return
    adb, emulator = tool(manager, 'adb'), tool(manager, 'emulator')
    env = {**os.environ}
    if resource.get('avd_home'):
        env['ANDROID_AVD_HOME'] = os.path.expanduser(resource['avd_home'])
    serial = f'emulator-{resource["port"]}'
    devices = android_devices(adb)
    # The same writable AVD must not be launched on a second console port.
    for other in devices:
        if other.startswith('emulator-') and avd_name(adb, other) == resource['avd'] and other != serial:
            raise ManagerError(f'AVD already running at {other}; refusing duplicate launch')
    if serial in devices:
        if avd_name(adb, serial) != resource['avd']:
            raise ManagerError('Configured Android serial is occupied by another AVD')
        owned = runtime and runtime['pid'] and process_alive(runtime['pid'], runtime['start'])
        if not owned and not resource['allow_attach']:
            raise ManagerError('Android emulator started outside manager; refusing to attach')
    else:
        # A prior manager launch may not have appeared in adb yet.
        owned = runtime and runtime['pid'] and process_alive(runtime['pid'], runtime['start'])
        if not owned:
            if resource['avd'] not in call([emulator, '-list-avds'], env=env).splitlines():
                raise ManagerError('Configured Android AVD does not exist')
            if not ports_free(resource['port']):
                raise ManagerError('Android console/adb ports occupied; refusing to launch')
            logs = manager.state_dir / 'logs'
            logs.mkdir(exist_ok=True, mode=0o700)
            # Hash IDs rather than allowing resource names to construct paths.
            import hashlib
            log_path = logs / (hashlib.sha256(resource['id'].encode()).hexdigest()[:16] + '.log')
            with log_path.open('ab') as log:
                child, gate = spawn_gated([emulator, '-avd', resource['avd'], '-port', str(resource['port']), '-no-snapshot-save'], env=env, stdout=log, stderr=log)
            try:
                manager.mark_runtime(resource, 'starting', child.pid)
                os.write(gate, b'1')
            finally:
                os.close(gate)
            runtime = manager.runtime(resource)
    while time.monotonic() < deadline:
        devices = android_devices(adb)
        if devices.get(serial) == 'device':
            if avd_name(adb, serial) != resource['avd']:
                raise ManagerError('Android serial changed AVD during boot')
            value = call([adb, '-s', serial, 'shell', 'getprop', 'sys.boot_completed'], 10)
            if value == '1':
                manager.mark_runtime(resource, 'ready', runtime['pid'] if runtime else None)
                return
        if runtime and runtime['pid'] and not process_alive(runtime['pid'], runtime['start']):
            raise ManagerError('Android emulator exited during boot; inspect state_dir/logs')
        time.sleep(.5)
    raise ManagerError('Android boot timed out; runtime is left running for safe recovery')


def discover(manager, kind):
    if kind == 'ios':
        return json.loads(call([tool(manager, 'xcrun'), 'simctl', 'list', 'devices', '--json']))
    adb, emulator = tool(manager, 'adb'), tool(manager, 'emulator')
    return {'avds':call([emulator, '-list-avds']).splitlines(), 'devices':android_devices(adb)}
