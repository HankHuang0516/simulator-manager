"""Only explicit device IDs/serials. Private idle shutdown only; never erase or kill a shared server."""
import json
import os
import re
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


def android_devices(adb, timeout=30):
    return {v[0]:v[1] for v in (line.split() for line in call([adb, 'devices'], timeout).splitlines()[1:]) if len(v)>=2}


def avd_name(adb, serial, timeout=10):
    lines = call([adb, '-s', serial, 'emu', 'avd', 'name'], timeout).splitlines()
    return next((line.strip() for line in lines if line.strip() and line.strip() != 'OK'), '')


def android_inventory(adb, deadline):
    """Require a complete, rechecked inventory; never skip unidentified emulators."""
    last_error = 'Android device inventory is not ready'
    while time.monotonic() < deadline:
        try:
            devices = android_devices(adb, max(.001, min(10, deadline-time.monotonic())))
            names = {}
            for serial in devices:
                if serial.startswith('emulator-'):
                    if time.monotonic() >= deadline:
                        raise ManagerError('Android inventory deadline reached')
                    names[serial] = avd_name(adb, serial, max(.001, min(10, deadline-time.monotonic())))
                    if not names[serial]:
                        raise ManagerError(f'Android AVD name unavailable for {serial}')
            if time.monotonic() >= deadline:
                raise ManagerError('Android inventory deadline reached')
            fresh = android_devices(adb, max(.001, min(10, deadline-time.monotonic())))
            if fresh == devices and time.monotonic() < deadline:
                return devices, names
            last_error = 'Android inventory changed while checking AVD identities'
        except ManagerError as e:
            last_error = str(e)
        time.sleep(max(0, min(.25, deadline-time.monotonic())))
    raise ManagerError(f'Android inventory did not become fully identifiable before boot deadline: {last_error}')


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


def disk_preflight(manager):
    free = shutil.disk_usage(manager.state_dir).free/(1024**3)
    minimum = manager.config['monitor']['low_disk_gib']
    if free<minimum:
        raise ManagerError(f'New simulator boot deferred: {free:.2f} GiB free disk; require at least {minimum:g} GiB. Release and retry after freeing space.')


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
            disk_preflight(manager)
            manager.mark_runtime(resource, 'starting')
            call([xcrun, 'simctl', 'boot', resource['udid']], max(.1, deadline-time.monotonic()))
        else:
            if not runtime:
                raise ManagerError(f'iOS device transitioning outside manager: {device["state"]}')
        call([xcrun, 'simctl', 'bootstatus', resource['udid']], max(.1, deadline-time.monotonic()))
        manager.mark_runtime(resource, 'ready')
        return
    adb, emulator = tool(manager, 'adb'), tool(manager, 'emulator')
    if resource.get('avd_home'):
        manifest = Path(resource['avd_home'])/(resource['avd']+'.ini')
        from .provision import ini_fields
        fields = ini_fields(manifest.read_text())
        target = re.fullmatch(r'android-(\d+)(?:-ext\d+)?',fields.get('target','').strip())
        if not target or int(target.group(1))<3:
            raise ManagerError('Android AVD target is invalid. New AVDs are corrected at creation; for an idle private AVD, acquire a lease and run repair-android-target TOKEN before boot. No VM was launched.')
    env = {**os.environ}
    if resource.get('avd_home'):
        env['ANDROID_AVD_HOME'] = os.path.expanduser(resource['avd_home'])
    serial = f'emulator-{resource["port"]}'
    while True:
        devices, names = android_inventory(adb, deadline)
        # The same writable AVD must not be launched on a second console port.
        for other in devices:
            if other.startswith('emulator-') and names[other] == resource['avd'] and other != serial:
                raise ManagerError(f'AVD already running at {other}; refusing duplicate launch')
        runtime = manager.runtime(resource)
        owned = runtime and runtime['pid'] and process_alive(runtime['pid'], runtime['start'])
        if serial in devices:
            if names[serial] != resource['avd']:
                raise ManagerError('Configured Android serial is occupied by another AVD')
            if not owned and not resource['allow_attach']:
                raise ManagerError('Android emulator started outside manager; refusing to attach')
            break
        # A prior manager launch may not have appeared in adb yet. Reuse its
        # live process; otherwise wait for exiting console sockets, never kill.
        if owned or ports_free(resource['port']):
            break
        if time.monotonic() >= deadline:
            raise ManagerError('Android console/adb ports occupied until boot deadline; refusing to launch')
        time.sleep(max(0, min(.25, deadline-time.monotonic())))
    if serial not in devices and not owned:
        disk_preflight(manager)
        if time.monotonic() >= deadline:
            raise ManagerError('Android boot deadline reached before launch')
        if resource['avd'] not in call([emulator, '-list-avds'], max(.001, min(30, deadline-time.monotonic())), env=env).splitlines():
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
    last_error = ''
    while time.monotonic() < deadline:
        try:
            devices = android_devices(adb, max(.001, min(10, deadline-time.monotonic())))
            name = avd_name(adb, serial, max(.001, min(10, deadline-time.monotonic()))) if devices.get(serial) == 'device' else None
            value = call([adb, '-s', serial, 'shell', 'getprop', 'sys.boot_completed'], max(.001, min(10, deadline-time.monotonic()))) if name == resource['avd'] else None
        except ManagerError as e:
            last_error = str(e)
            name, value = None, None
        if name is not None and name != resource['avd']:
            raise ManagerError('Android serial changed AVD during boot')
        if value is not None:
            if value == '1' and time.monotonic() < deadline:
                manager.mark_runtime(resource, 'ready', runtime['pid'] if runtime else None)
                return
        if runtime and runtime['pid'] and not process_alive(runtime['pid'], runtime['start']):
            raise ManagerError('Android emulator exited during boot; inspect state_dir/logs')
        time.sleep(max(0, min(.5, deadline-time.monotonic())))
    raise ManagerError(f'Android boot timed out; runtime is left running for safe recovery. {last_error}')


def discover(manager, kind):
    if kind == 'ios':
        return json.loads(call([tool(manager, 'xcrun'), 'simctl', 'list', 'devices', '--json']))
    adb, emulator = tool(manager, 'adb'), tool(manager, 'emulator')
    return {'avds':call([emulator, '-list-avds']).splitlines(), 'devices':android_devices(adb)}


def stop_managed(manager, resource):
    """Only private dynamic environments selected while unleased may be stopped."""
    row = manager.db.execute('SELECT phase FROM environments WHERE resource=?',(resource['id'],)).fetchone()
    if not row or row['phase']!='stopping' or manager.db.execute('SELECT 1 FROM leases WHERE resource=?',(resource['id'],)).fetchone():
        raise ManagerError('Shutdown requires an unleased private environment reserved for stopping')
    runtime = manager.runtime(resource)
    if not runtime:
        raise ManagerError('No current-boot runtime provenance; shutdown refused')
    if resource['kind']=='ios':
        xcrun = tool(manager,'xcrun')
        devices = json.loads(call([xcrun,'simctl','list','devices','--json']))['devices']
        device = next((d for ds in devices.values() for d in ds if d['udid'].upper()==resource['udid'].upper()),None)
        if not device:
            raise ManagerError('Private simulator disappeared')
        if device['state']!='Shutdown':
            call([xcrun,'simctl','shutdown',resource['udid']],30)
        devices = json.loads(call([xcrun,'simctl','list','devices','--json']))['devices']
        if not any(d['udid'].upper()==resource['udid'].upper() and d['state']=='Shutdown' for ds in devices.values() for d in ds):
            raise ManagerError('Private simulator has not finished shutting down')
        return
    adb = tool(manager,'adb')
    serial = f'emulator-{resource["port"]}'
    live = runtime['pid'] and process_alive(runtime['pid'],runtime['start'])
    devices = android_devices(adb)
    if serial in devices:
        if not live or avd_name(adb,serial)!=resource['avd']:
            raise ManagerError('Android runtime ownership changed; shutdown refused')
        call([adb,'-s',serial,'emu','kill'],10)
    elif live:
        raise ManagerError('Android launch is live but not identifiable through adb; shutdown deferred')
    deadline = time.monotonic()+15
    while runtime['pid'] and process_alive(runtime['pid'],runtime['start']) and time.monotonic()<deadline:
        time.sleep(.1)
    if runtime['pid'] and process_alive(runtime['pid'],runtime['start']):
        raise ManagerError('Private emulator has not exited; reservation retained')
