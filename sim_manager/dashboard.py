"""Lazy, user-local native macOS dashboard. No simulator control actions."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import signal
import subprocess
import sys
import tempfile
import time
from . import __version__


def build(root=None, state_dir=None):
    if sys.platform != 'darwin':
        raise ValueError('The floating dashboard requires macOS 13+ and Xcode command line tools.')
    root = Path(root or Path(__file__).resolve().parent.parent).resolve()
    source = root/'dashboard/SimulatorManager.swift'
    app = root/'Simulator Manager.app'
    state = Path(state_dir or os.environ.get('SIM_MANAGER_STATE_DIR',str(Path.home()/'Library/Application Support/simulator-manager'))).expanduser().resolve()
    digest = hashlib.sha256(source.read_bytes()+__version__.encode()+str(root/'bin/sim-manager').encode()+str(state).encode()).hexdigest()
    with (root/'.dashboard-build.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        exe = app/'Contents/MacOS/SimulatorManager'
        marker = app/'Contents/Resources/source.sha256'
        if exe.is_file() and marker.is_file() and marker.read_text().strip() == digest:
            return app
        exe.parent.mkdir(parents=True,exist_ok=True)
        marker.parent.mkdir(parents=True,exist_ok=True)
        temporary = exe.with_suffix('.building')
        try:
            subprocess.run(['xcrun','swiftc','-parse-as-library','-swift-version','5','-O','-target',
                            os.uname().machine+'-apple-macosx13.0',str(source),
                            '-o',str(temporary)],check=True,timeout=180)
            temporary.replace(exe)
        except (subprocess.SubprocessError, OSError) as e:
            raise ValueError('Dashboard build failed. Install Xcode command line tools and retry: '+str(e)) from e
        finally:
            temporary.unlink(missing_ok=True)
        info = {'CFBundleName':'Simulator Manager','CFBundleDisplayName':'Simulator Manager',
                'CFBundleIdentifier':'com.hankhuang.simulator-manager.dashboard',
                'CFBundleExecutable':'SimulatorManager','CFBundlePackageType':'APPL',
                'CFBundleShortVersionString':__version__,'CFBundleVersion':'2',
                'LSMinimumSystemVersion':'13.0','LSUIElement':False,
                'NSHighResolutionCapable':True,
                'SimulatorManagerCLI':str(root/'bin/sim-manager'),
                'SimulatorManagerState':str(state),
                'SimulatorManagerManaged':True}
        with (app/'Contents/Info.plist').open('wb') as f:
            plistlib.dump(info,f)
        marker.write_text(digest+'\n')
    return app


def running_dashboard_pids(app):
    """Return only processes executing this exact managed app binary path."""
    executable = str((Path(app)/'Contents/MacOS/SimulatorManager').resolve())
    try:
        result = subprocess.run(['/bin/ps','-axo','pid=,command='],capture_output=True,
                                text=True,check=True,timeout=5)
    except (OSError, subprocess.SubprocessError):
        return []
    pids = []
    for line in result.stdout.splitlines():
        match = re.match(r'\s*(\d+)\s+(.*)$',line)
        if not match:
            continue
        command = match.group(2)
        if command == executable or command.startswith(executable+' '):
            pids.append(int(match.group(1)))
    return pids


def stop_existing_dashboard(app):
    """Gracefully stop an exact managed dashboard so an upgrade loads new code."""
    pids = running_dashboard_pids(app)
    for pid in pids:
        try:
            os.kill(pid,signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic()+5
    pending = set(pids)
    while pending and time.monotonic()<deadline:
        for pid in list(pending):
            try:
                os.kill(pid,0)
            except ProcessLookupError:
                pending.discard(pid)
            except PermissionError as e:
                raise ValueError('Could not replace the running Simulator Manager dashboard') from e
        if pending:
            time.sleep(.05)
    if pending:
        raise ValueError('Running Simulator Manager dashboard did not close; upgrade deferred')


def install_user_app(app, target=None):
    app = Path(app).expanduser().resolve()
    target = Path(target or Path.home()/'Applications/Simulator Manager.app').expanduser().resolve()
    target.parent.mkdir(parents=True,exist_ok=True)
    if app != target:
        stop_existing_dashboard(app)
    if target.exists():
        plist = target/'Contents/Info.plist'
        try:
            with plist.open('rb') as f:
                info = plistlib.load(f)
        except (OSError, plistlib.InvalidFileException) as e:
            raise ValueError('Refusing to replace an unrelated application at '+str(target)) from e
        if info.get('CFBundleIdentifier')!='com.hankhuang.simulator-manager.dashboard' or not info.get('SimulatorManagerManaged'):
            raise ValueError('Refusing to replace an unrelated application at '+str(target))
        stop_existing_dashboard(target)
    temporary = Path(tempfile.mkdtemp(prefix='.Simulator Manager.',dir=target.parent))/'Simulator Manager.app'
    try:
        subprocess.run(['/usr/bin/ditto',str(app),str(temporary)],check=True,timeout=30)
        if target.exists():
            subprocess.run(['/bin/rm','-rf',str(target)],check=True,timeout=30)
        temporary.replace(target)
    finally:
        if temporary.parent.exists():
            subprocess.run(['/bin/rm','-rf',str(temporary.parent)],check=False,timeout=30)
    return target


def launch(state_dir=None, build_only=False, root=None, install_app=False, onboarding=False):
    root = Path(root or Path(__file__).resolve().parent.parent).resolve()
    state = Path(state_dir or os.environ.get('SIM_MANAGER_STATE_DIR',str(Path.home()/'Library/Application Support/simulator-manager'))).expanduser().resolve()
    app = build(root,state)
    installed = install_user_app(app) if install_app else None
    launch_app = installed or app
    if not build_only:
        try:
            if onboarding and not installed:
                stop_existing_dashboard(launch_app)
            args = ['open']
            args.extend([str(launch_app),'--args','--cli',str(root/'bin/sim-manager'),
                         '--state-dir',str(state)])
            if onboarding:
                args.append('--onboarding')
            subprocess.run(args,check=True)
        except subprocess.SubprocessError as e:
            raise ValueError('Could not open the dashboard: '+str(e)) from e
    return {'dashboard':str(launch_app),'installed_app':str(installed) if installed else None,
            'opened':not build_only,'onboarding':onboarding,'controls':'view-and-guidance','refresh_seconds':2}


if __name__ == '__main__':
    print(json.dumps(launch(build_only='--build-only' in sys.argv)))
