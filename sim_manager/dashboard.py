"""Lazy, user-local native macOS dashboard. No simulator control actions."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys


def build(root=None):
    if sys.platform != 'darwin':
        raise ValueError('The floating dashboard requires macOS 13+ and Xcode command line tools.')
    root = Path(root or Path(__file__).resolve().parent.parent).resolve()
    source = root/'dashboard/SimulatorManager.swift'
    app = root/'Simulator Manager.app'
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
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
                'CFBundleShortVersionString':'2.0.0','CFBundleVersion':'2',
                'LSMinimumSystemVersion':'13.0','LSUIElement':True,
                'NSHighResolutionCapable':True}
        with (app/'Contents/Info.plist').open('wb') as f:
            plistlib.dump(info,f)
        marker.write_text(digest+'\n')
    return app


def launch(state_dir=None, build_only=False, root=None):
    root = Path(root or Path(__file__).resolve().parent.parent).resolve()
    app = build(root)
    if not build_only:
        state = Path(state_dir or os.environ.get('SIM_MANAGER_STATE_DIR',str(Path.home()/'Library/Application Support/simulator-manager'))).expanduser().resolve()
        try:
            subprocess.run(['open',str(app),'--args','--cli',str(root/'bin/sim-manager'),
                            '--state-dir',str(state)],check=True)
        except subprocess.SubprocessError as e:
            raise ValueError('Could not open the dashboard: '+str(e)) from e
    return {'dashboard':str(app),'opened':not build_only,'controls':'view-only','refresh_seconds':2}


if __name__ == '__main__':
    print(json.dumps(launch(build_only='--build-only' in sys.argv)))
