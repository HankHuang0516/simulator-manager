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
from .core import ManagerError, load_config, process_alive, group_alive
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


def ini_fields(text):
    return {key.strip():value.strip() for line in text.splitlines()
            if '=' in line and not line.lstrip().startswith(('#',';'))
            for key,value in [line.split('=',1)]}


def normalize_created_android_target(home, name, api_level):
    """Caller proves new creation or a valid, idle private-environment lease."""
    manifest = home/(name+'.ini')
    content_dir = home/(name+'.avd')
    if manifest.is_symlink() or not manifest.is_file() or content_dir.is_symlink() or not content_dir.is_dir():
        raise ManagerError('AVD creation did not produce local manifest/content artifacts')
    text = manifest.read_text(encoding='utf-8')
    fields = ini_fields(text)
    if not fields.get('path') or Path(fields['path']).resolve()!=content_dir.resolve():
        raise ManagerError('Created AVD manifest points outside the assigned content directory')
    current = fields.get('target','')
    valid = re.fullmatch(r'android-(\d+)(?:-ext\d+)?',current)
    if valid and int(valid.group(1))==api_level:
        return
    # Older avdmanager versions write android-0 for Major.Minor images. QEMU
    # parses the root target as an integer API; retain the full image package
    # path, but do not replace zero with a decimal/unknown API value.
    lines = [line for line in text.splitlines() if line.split('=',1)[0].strip()!='target']
    lines.append(f'target=android-{api_level}')
    fd, temporary = tempfile.mkstemp(prefix='.avd-target-',dir=home)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            f.write('\n'.join(lines)+'\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary,manifest)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def repair_leased_android_target(manager, token):
    """Explicit metadata-only recovery for the borrower's idle private AVD."""
    with manager.transaction():
        lease = manager.get_lease(token)
        resource = json.loads(lease['spec'])
        env = manager.db.execute('SELECT * FROM environments WHERE resource=?',(lease['resource'],)).fetchone()
        if resource['kind']!='android' or not env or env['session']!=lease['session'] or env['project']!=lease['project']:
            raise ManagerError('Target repair requires a lease for your assigned private Android environment')
        if env['phase']!='ready' or env['running'] or lease['operation'] or lease['activity_group']:
            raise ManagerError('Target repair requires no running VM or tracked work')
        home = Path(resource['avd_home']).resolve()
        expected = manager.state_dir/'environments'/resource['id']/'avds'
        if home!=expected.resolve():
            raise ManagerError('Private AVD home does not match its assigned environment')
        runtime = manager.runtime(resource)
        if runtime and runtime['pid'] and (process_alive(runtime['pid'],runtime['start']) or group_alive(runtime['pid'],runtime['boot'])):
            raise ManagerError('Private Android runtime is still active; repair deferred')
        if not ports_free(resource['port']):
            raise ManagerError('Android ports occupied; target repair deferred')
        content = home/(resource['avd']+'.avd')
        if content.is_symlink():
            raise ManagerError('Private AVD content directory is not local')
        fields = ini_fields((content/'config.ini').read_text())
        image_value = fields.get('image.sysdir.1','').strip()
        if not image_value:
            raise ManagerError('AVD image metadata is missing; repair refused')
        sdk = sdk_home(manager)
        image = (sdk/image_value).resolve()
        try:
            image.relative_to((sdk/'system-images').resolve())
        except ValueError as e:
            raise ManagerError('AVD image points outside the configured SDK system images') from e
        props = ini_fields((image/'source.properties').read_text())
        api = re.fullmatch(r'(\d+)(?:\.\d+)?',props.get('AndroidVersion.ApiLevel','').strip())
        if not api or int(api.group(1))<3 or not (image/'system.img').is_file():
            raise ManagerError('Installed image API metadata is invalid; repair refused')
        manifest = home/(resource['avd']+'.ini')
        before = manifest.read_bytes()
        normalize_created_android_target(home,resource['avd'],int(api.group(1)))
        changed = before!=manifest.read_bytes()
        manager.event('android-target-repaired',resource['id'],lease['session'],f'android-{api.group(1)}')
        return {'repaired':changed,'resource_id':resource['id'],'target':f'android-{api.group(1)}','data_preserved':True}


def android_resource(manager, reserved_port=None, private_home=None):
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
        version = re.match(r'^system-images;android-(\d+)(?:\.(\d+))?(?:-ext(\d+))?;',identifier)
        if not version or int(version.group(1))<3:
            continue
        images.append((tuple(int(v or 0) for v in version.groups()),identifier))
    if not images:
        raise ManagerError(f'No installed {arch} Android system image; install one in SDK Manager first')
    used = {r['port'] for p in manager.config['pools'].values() for r in p['resources'] if r['kind']=='android'}
    port = reserved_port if reserved_port is not None else next((p for p in range(5556,5683,2) if p not in used and ports_free(p)),None)
    if port is None:
        raise ManagerError('No unoccupied Android console/adb port pair available')
    suffix = secrets.token_hex(6)
    name = f'Codex_Shared_{suffix}'
    home = private_home or manager.state_dir/'avds'
    home.mkdir(parents=True,exist_ok=True,mode=0o700)
    if (home/(name+'.ini')).exists() or (home/(name+'.ini')).is_symlink() or (home/(name+'.avd')).exists() or (home/(name+'.avd')).is_symlink():
        raise ManagerError('Generated Android AVD name is already occupied; creation refused')
    version, image_identifier = max(images)
    env = {**os.environ,'ANDROID_HOME':str(sdk),'ANDROID_AVD_HOME':str(home)}
    # No --force, no download, no mutation of ~/.android/avd.
    command = [avdmanager,'create','avd','-n',name,'-k',image_identifier,'-p',str(home/(name+'.avd'))]
    try:
        p = subprocess.run(command,input='no\n',capture_output=True,text=True,timeout=30,env=env)
    except (OSError,subprocess.TimeoutExpired) as e:
        raise ManagerError(f'Dedicated Android AVD creation failed: {e}') from e
    if p.returncode:
        raise ManagerError(f'avdmanager failed: {p.stderr.strip()}')
    normalize_created_android_target(home,name,version[0])
    return {'id':f'android-{suffix}','kind':'android','avd':name,'port':port,'avd_home':str(home),
            'api_level':version[0],'system_image':image_identifier,
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
