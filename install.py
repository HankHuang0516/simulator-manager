#!/usr/bin/env python3
"""User-local install, no sudo, no downloads, no shell rc edits."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    if sys.version_info < (3, 9):
        raise SystemExit('Python 3.9+ required. Set SIM_MANAGER_PYTHON to its executable.')
    p = argparse.ArgumentParser()
    p.add_argument('--prefix', type=Path, default=Path.home()/'.local/share/simulator-manager')
    p.add_argument('--bin-dir', type=Path, default=Path.home()/'.local/bin')
    p.add_argument('--skill-dir', type=Path, default=Path(os.environ.get('CODEX_HOME', str(Path.home()/'.codex')))/'skills')
    p.add_argument('--state-dir', type=Path, default=Path.home()/'Library/Application Support/simulator-manager')
    p.add_argument('--upgrade', action='store_true', help='Replace this managed install; requires no active leases/queue')
    a = p.parse_args()
    os.umask(0o077)
    source = Path(__file__).resolve().parent
    prefix, binary, skill, state = (a.prefix.expanduser().resolve(), a.bin_dir.expanduser().resolve(),
                                  a.skill_dir.expanduser().resolve()/'simulator-manager', a.state_dir.expanduser().resolve())
    marker = prefix/'.simulator-manager-install'
    target = binary/'sim-manager'
    if prefix == source or source in prefix.parents:
        raise SystemExit('Install prefix must be outside source project')
    if prefix.exists() and (not marker.is_file() or not a.upgrade):
        raise SystemExit('Prefix already exists; managed installations require --upgrade')
    if skill.exists() and not (a.upgrade and (skill/'.simulator-manager-install').is_file()):
        raise SystemExit('Skill directory exists; refusing to overwrite an unrelated skill')
    if target.exists() or target.is_symlink():
        if not (a.upgrade and target.is_symlink() and target.resolve() == prefix/'bin/sim-manager'):
            raise SystemExit('sim-manager executable already exists; refusing to overwrite')
    if a.upgrade and (state/'state.sqlite3').exists():
        import sqlite3
        db = sqlite3.connect(state/'state.sqlite3')
        try:
            if db.execute('SELECT 1 FROM leases UNION ALL SELECT 1 FROM queue LIMIT 1').fetchone():
                raise SystemExit('Drain active leases and queue before upgrading')
        finally:
            db.close()
        if (prefix/'sim_manager/watchdog.py').exists():
            subprocess.run([sys.executable,str(source/'bin/sim-manager'),'watch','--stop','--state-dir',str(state),'--json'],check=True)
    prefix.mkdir(parents=True, exist_ok=True)
    for item in ('sim_manager','bin','config','skill','dashboard','assets'):
        shutil.copytree(source/item, prefix/item, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    # Pin the interpreter used during installation, independent of future PATH.
    launcher = prefix/'bin/sim-manager'
    launcher.write_text('#!' + sys.executable + '\n' +
                        'import sys\nfrom pathlib import Path\n' +
                        'sys.path.insert(0, str(Path(__file__).resolve().parent.parent))\n' +
                        'from sim_manager.cli import main\nraise SystemExit(main())\n')
    # A shebang cannot portably contain spaces; use a shell trampoline in that case.
    if ' ' in sys.executable:
        import shlex
        launcher.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' ' +
                            shlex.quote(str(prefix/'bin/entry.py')) + ' "$@"\n')
        (prefix/'bin/entry.py').write_text((source/'bin/sim-manager').read_text())
    launcher.chmod(0o755)
    marker.write_text('1\n')
    binary.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        target.unlink()
    target.symlink_to(launcher)
    skill.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source/'skill/simulator-manager', skill, dirs_exist_ok=True)
    (skill/'.simulator-manager-install').write_text('1\n')
    # Resolve CLI for Codex even if its process has not picked up shell PATH.
    (skill/'references/installation.md').write_text(
        'Installed CLI: `' + str(target) + '`\n\nShared state: `' + str(state) +
        '`\n\nSet `SIM_MANAGER_STATE_DIR` to this shared state when using a custom location.\n')
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    state.chmod(0o700)
    if not (state/'config.json').exists():
        shutil.copy2(source/'config/default.json', state/'config.json')
    (state/'config.json').chmod(0o600)
    # Isolated --state-dir option is respected. Does not boot or shut down devices.
    subprocess.run([str(target),'validate-config','--state-dir',str(state),'--json'], check=True)
    print(f'Installed CLI: {target}\nSkill: {skill}\nConfiguration: {state / "config.json"}')
    print('Add the bin directory to PATH if needed; no shell configuration was modified.')


if __name__ == '__main__':
    main()
