"""Create dedicated, shutdown devices using already-installed SDK components."""
import copy
import json
import os
from pathlib import Path
import platform
import re
import secrets
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from .core import ManagerError, load_config
from .providers import call, ports_free, tool


def save_config(manager, config):
    # Caller holds BEGIN IMMEDIATE. Never replace malformed or changed config.
    current = load_config(manager.config_path)
    if manager.canonical(current) != manager.canonical(manager.config):
        raise ManagerError('Configuration changed during setup; retry after draining')
    fd, path = tempfile.mkstemp(prefix='.config-', dir=manager.config_path.resolve().parent)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(config, f, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(path, manager.config_path)
    finally:
        if os.path.exists(path):
            os.unlink(path)
    # The on-disk config remains authoritative even if a host crash occurs
    # between replace and COMMIT; the next idle Manager loads it again.
    manager.config = config
    manager.requested = config
    manager.db.execute("INSERT OR REPLACE INTO meta VALUES('config',?)", (manager.canonical(config),))


def ios_resource(manager):
    xcrun = tool(manager, 'xcrun')
    inventory = json.loads(call([xcrun, 'simctl', 'list', '--json']))
    runtimes = [r for r in inventory.get('runtimes', [])
                if r.get('isAvailable') and r['identifier'].startswith('com.apple.CoreSimulator.SimRuntime.iOS-')]
    if not runtimes:
        raise ManagerError('No installed, available iOS runtime; install one in Xcode first')
    runtime = max(runtimes, key=lambda r:tuple(int(v) for v in re.findall(r'\d+',r.get('version','0'))))
    candidates = runtime.get('supportedDeviceTypes') or inventory.get('devicetypes', [])
    phones = [d for d in candidates if d.get('name','').startswith('iPhone')]
    if not phones:
        raise ManagerError('No compatible iPhone device type available')
    device = max(phones, key=lambda d:tuple(int(v) for v in re.findall(r'\d+',d['name'])))
    suffix = secrets.token_hex(6)
    # Unique name, no adoption of a personal simulator and no boot.
    udid = call([xcrun,'simctl','create',f'Codex Shared iOS {suffix}',device['identifier'],runtime['identifier']])
    if not re.fullmatch(r'[A-Fa-f0-9-]{36}',udid):
        raise ManagerError('simctl create returned an unexpected device identifier')
    return {'id':f'ios-{suffix}','kind':'ios','udid':udid,'cost':1,'enabled':True,'allow_attach':False}


def sdk_home(manager):
    explicit = manager.config.get('android_sdk')
    return Path(explicit or os.environ.get('ANDROID_SDK_ROOT') or os.environ.get('ANDROID_HOME') or
                Path.home()/'Library/Android/sdk').expanduser().resolve()


def android_resource(manager):
    tool(manager,'adb')
    tool(manager,'emulator')
    avdmanager = tool(manager,'avdmanager')
    sdk = sdk_home(manager)
    arch = 'arm64-v8a' if platform.machine().lower() in ('arm64','aarch64') else 'x86_64'
    images = []
    for package in (sdk/'system-images').glob('*/*/*/package.xml'):
        if package.parent.name != arch or not (package.parent/'system.img').is_file():
            continue
        try:
            root = ET.parse(package).getroot()
            local = next(e for e in root.iter() if e.tag.rsplit('}',1)[-1] == 'localPackage')
            identifier = local.attrib['path']
        except (ET.ParseError,StopIteration,KeyError):
            continue
        if not identifier.startswith('system-images;'):
            continue
        version = re.search(r'android-(\d+)',identifier)
        images.append((int(version.group(1)) if version else 0,identifier))
    if not images:
        raise ManagerError(f'No installed {arch} Android system image; install one in SDK Manager first')
    used = {r['port'] for p in manager.config['pools'].values() for r in p['resources'] if r['kind']=='android'}
    port = next((p for p in range(5556,5683,2) if p not in used and ports_free(p)),None)
    if port is None:
        raise ManagerError('No unoccupied Android console/adb port pair available')
    suffix = secrets.token_hex(6)
    name = f'Codex_Shared_{suffix}'
    home = manager.state_dir/'avds'
    home.mkdir(exist_ok=True,mode=0o700)
    env = {**os.environ,'ANDROID_HOME':str(sdk),'ANDROID_AVD_HOME':str(home)}
    # No --force, no download, no mutation of ~/.android/avd.
    command = [avdmanager,'create','avd','-n',name,'-k',max(images)[1],'-p',str(home/(name+'.avd'))]
    try:
        p = subprocess.run(command,input='no\n',capture_output=True,text=True,timeout=30,env=env)
    except (OSError,subprocess.TimeoutExpired) as e:
        raise ManagerError(f'Dedicated Android AVD creation failed: {e}') from e
    if p.returncode:
        raise ManagerError(f'avdmanager failed: {p.stderr.strip()}')
    return {'id':f'android-{suffix}','kind':'android','avd':name,'port':port,'avd_home':str(home),
            'cost':1,'enabled':True,'allow_attach':False}


def prepare(manager, kinds=('ios','android')):
    results = {}
    for kind in kinds:
        with manager.transaction():
            manager.sweep()
            current = load_config(manager.config_path)
            if manager.canonical(current) != manager.canonical(manager.config):
                results[kind] = {'ready':False,'reason':'Configuration change pending; drain and retry'}
                continue
            pool = current['pools'].get(kind)
            if pool and pool['capacity'] and any(r['enabled'] and r['kind']==kind for r in pool['resources']):
                results[kind] = {'ready':True,'created':False}
                continue
            # Never change an intentionally disabled/configured pool.
            if pool and (pool['capacity']==0 or pool['resources']):
                results[kind] = {'ready':False,'reason':'Existing pool is disabled or configured; configuration preserved'}
                continue
            if manager.db.execute('SELECT 1 FROM leases UNION ALL SELECT 1 FROM queue LIMIT 1').fetchone():
                results[kind] = {'ready':False,'reason':'Resources are in use; dedicated device setup deferred'}
                continue
            try:
                resource = ios_resource(manager) if kind=='ios' else android_resource(manager)
                updated = copy.deepcopy(current)
                updated['pools'][kind] = {'capacity':1,'resources':[resource]}
                save_config(manager,updated)
                manager.event('device-created',resource['id'],detail=kind)
                results[kind] = {'ready':True,'created':True,'resource_id':resource['id']}
            except ManagerError as e:
                results[kind] = {'ready':False,'reason':str(e)}
    return results
