"""Explicit migration from persistent private devices to a warm shared pool."""
import copy
import json
from pathlib import Path
import re
import shutil
import subprocess

from .core import ManagerError, boot_matches, group_alive, load_config, process_alive
from .provision import ini_fields, normalize_created_android_target, save_config, sdk_home
from .providers import call, ports_free, tool


def _count(manager, table):
    return manager.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]


def _android_process_mentions(avd):
    result = subprocess.run(['/bin/ps', '-axo', 'command='], capture_output=True, text=True, timeout=5)
    if result.returncode:
        raise ManagerError('Cannot verify Android emulator processes before private cleanup')
    pattern = re.compile(r'(?:^|\s)-avd\s+' + re.escape(avd) + r'(?=\s|$)')
    return any(pattern.search(line) and re.search(r'(?:^|/)(?:emulator|qemu-system-[^/\s]+)(?:\s|$)', line)
               for line in result.stdout.splitlines())


def switch_shared(manager):
    """Change admission only after a drained window; never reassign private data."""
    with manager.transaction():
        manager.sweep()
        if _count(manager, 'leases') or _count(manager, 'queue'):
            raise ManagerError('Shared-pool switch requires zero leases and FIFO waiters')
        current = load_config(manager.config_path)
        if manager.canonical(current) != manager.canonical(manager.config):
            raise ManagerError('Configuration changed or is pending; retry after draining')
        for kind in ('ios', 'android'):
            pool = current['pools'].get(kind, {})
            if not pool.get('capacity') or not any(r['enabled'] and r['kind'] == kind for r in pool.get('resources', [])):
                raise ManagerError(f'No ready shared {kind} resource; run setup {kind} first')
        ios_inventory = json.loads(call([tool(manager, 'xcrun'), 'simctl', 'list', 'devices', '--json']))
        for resource in current['pools']['ios']['resources']:
            if not resource['enabled']:
                continue
            device = next((d for devices in ios_inventory.get('devices', {}).values() for d in devices
                           if d.get('udid', '').upper() == resource['udid'].upper()), None)
            if not device or not device.get('isAvailable'):
                raise ManagerError('Configured shared iOS device is missing or unavailable')
            if device.get('state') == 'Booted' and not manager.runtime(resource) and not resource.get('allow_attach'):
                raise ManagerError('Shared iOS device is booted outside manager; switch deferred')
        # The historical Android fallback could have android-0 even with an
        # installed 36.1 image. Repair metadata only when the exact owned AVD
        # is offline; keep its userdata and all other AVDs untouched.
        repaired = []
        for resource in current['pools']['android']['resources']:
            if not resource['enabled']:
                continue
            home = Path(resource['avd_home']).resolve()
            if home != (manager.state_dir / 'avds').resolve() or not ports_free(resource['port']):
                raise ManagerError('Shared Android AVD is external or active; switch deferred')
            manifest = home / (resource['avd'] + '.ini')
            content = home / (resource['avd'] + '.avd')
            if manifest.is_symlink() or content.is_symlink() or not manifest.is_file() or not content.is_dir():
                raise ManagerError('Shared Android AVD artifacts are missing or ambiguous')
            fields = ini_fields((content / 'config.ini').read_text())
            image_value = fields.get('image.sysdir.1', '').strip()
            if not image_value:
                raise ManagerError('Shared Android image metadata is missing')
            sdk = sdk_home(manager)
            image = (sdk / image_value).resolve()
            try:
                image.relative_to((sdk / 'system-images').resolve())
            except ValueError as e:
                raise ManagerError('Shared Android image points outside installed SDK') from e
            props = ini_fields((image / 'source.properties').read_text())
            api = re.fullmatch(r'(\d+)(?:\.\d+)?', props.get('AndroidVersion.ApiLevel', '').strip())
            if not api or int(api.group(1)) < 3 or not (image / 'system.img').is_file():
                raise ManagerError('Shared Android image API is invalid')
            before = manifest.read_bytes()
            normalize_created_android_target(home, resource['avd'], int(api.group(1)))
            if before != manifest.read_bytes():
                repaired.append(resource['id'])
        updated = copy.deepcopy(current)
        updated['mode'] = 'traditional'
        save_config(manager, updated)
        manager.event('shared-mode-enabled', detail='warm FIFO')
        return {'mode': 'traditional', 'shared_resources': {kind: [r['id'] for r in updated['pools'][kind]['resources'] if r['enabled']]
                                                          for kind in ('ios', 'android')},
                'android_targets_repaired': repaired, 'private_environments_preserved': _count(manager, 'environments')}


def prune_private(manager):
    """Delete only offline manager-owned private devices after shared migration.

    Refusals are reported per resource. A failed/incomplete creation is kept
    quarantined because the SDK may have created an unacknowledged device.
    """
    with manager.transaction():
        manager.sweep()
        if manager.config['mode'] != 'traditional' or _count(manager, 'leases') or _count(manager, 'queue'):
            raise ManagerError('Private cleanup requires shared mode with zero leases and FIFO waiters')
        rows = [dict(r) for r in manager.db.execute('SELECT * FROM environments ORDER BY created')]
    deleted, blocked = [], []
    for row in rows:
        resource = row['resource']
        spec = json.loads(row['spec'])
        reason = None
        try:
            if row['phase'] not in ('ready', 'deleting') or row['running'] or spec.get('id') != resource or not resource.startswith('dynamic-' + row['pool'] + '-'):
                raise ManagerError('Environment is running, incomplete, or lacks exact private provenance')
            runtime = manager.db.execute('SELECT * FROM runtimes WHERE resource=?', (resource,)).fetchone()
            if runtime:
                if runtime['spec'] != manager.canonical(spec):
                    raise ManagerError('Runtime identity differs from private environment')
                if runtime['pid'] and boot_matches(runtime['boot'], manager.machine_boot) and (
                        process_alive(runtime['pid'], runtime['start']) or group_alive(runtime['pid'], runtime['boot'])):
                    raise ManagerError('Owned runtime is still live')
            root = manager.state_dir / 'environments' / resource
            if root.is_symlink():
                raise ManagerError('Private environment directory is a symlink')
            # Persist the deletion intent before any external SDK mutation so
            # an interrupted cleanup can resume without guessing provenance.
            with manager.transaction():
                if manager.config['mode'] != 'traditional' or manager.db.execute('SELECT 1 FROM leases WHERE resource=?', (resource,)).fetchone():
                    raise ManagerError('Environment became leased; cleanup deferred')
                manager.db.execute("UPDATE environments SET phase='deleting' WHERE resource=? AND phase IN ('ready','deleting') AND running=0", (resource,))
                if not manager.db.execute("SELECT 1 FROM environments WHERE resource=? AND phase='deleting'", (resource,)).fetchone():
                    raise ManagerError('Environment state changed during cleanup')
            if row['pool'] == 'ios':
                udid = spec.get('udid')
                if not udid:
                    raise ManagerError('Private iOS UDID was never acknowledged')
                if any(r.get('udid', '').upper() == udid.upper() for p in manager.config['pools'].values() for r in p['resources']):
                    raise ManagerError('Private UDID is also configured as shared')
                inventory = json.loads(call([tool(manager, 'xcrun'), 'simctl', 'list', 'devices', '--json']))
                device = next((d for devices in inventory.get('devices', {}).values() for d in devices if d.get('udid', '').upper() == udid.upper()), None)
                if device:
                    if device.get('state') != 'Shutdown' or not device.get('name', '').startswith('Codex Shared iOS '):
                        raise ManagerError('iOS device is booted or no longer has manager identity')
                    call([tool(manager, 'xcrun'), 'simctl', 'delete', udid], 60)
            elif row['pool'] == 'android':
                home = Path(spec.get('avd_home', '')).resolve()
                expected = root / 'avds'
                if home != expected.resolve() or expected.is_symlink() or not ports_free(spec['port']):
                    raise ManagerError('Private Android AVD is external, symlinked, or active')
                if any(r.get('port') == spec['port'] or r.get('avd') == spec.get('avd') for p in manager.config['pools'].values() for r in p['resources'] if r['kind'] == 'android'):
                    raise ManagerError('Private AVD identity is also configured as shared')
                if _android_process_mentions(spec['avd']):
                    raise ManagerError('Private Android AVD still appears in a live emulator process')
                manifest = expected / (spec['avd'] + '.ini')
                content = expected / (spec['avd'] + '.avd')
                if manifest.is_symlink() or content.is_symlink() or not manifest.is_file() or not content.is_dir():
                    raise ManagerError('Private Android AVD artifacts are missing or ambiguous')
                if Path(ini_fields(manifest.read_text()).get('path', '')).resolve() != content.resolve():
                    raise ManagerError('Private Android AVD manifest points elsewhere')
            else:
                raise ManagerError('Only mobile private environments can be pruned')
            if root.exists():
                shutil.rmtree(root)
            with manager.transaction():
                manager.db.execute('DELETE FROM runtimes WHERE resource=?', (resource,))
                manager.db.execute("DELETE FROM environments WHERE resource=? AND phase='deleting'", (resource,))
                manager.event('private-environment-deleted', resource, row['session'])
            deleted.append(resource)
        except (ManagerError, OSError, KeyError, ValueError, subprocess.TimeoutExpired) as e:
            reason = str(e)
        if reason:
            blocked.append({'resource': resource, 'reason': reason})
    return {'deleted': deleted, 'blocked': blocked, 'remaining': _count(manager, 'environments')}
