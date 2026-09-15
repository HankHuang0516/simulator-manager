#!/usr/bin/env python3
"""Idempotent session bootstrap. Installation is serialized across sessions."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def main():
    if sys.version_info < (3,9):
        raise SystemExit('Python 3.9+ required')
    p=argparse.ArgumentParser(description='Install once, then enable shared mode for this session')
    p.add_argument('--session')
    p.add_argument('--project',default=str(Path.cwd()))
    p.add_argument('--owner-pid',type=int)
    p.add_argument('--no-prepare',action='store_true',help='Skip dedicated device creation')
    p.add_argument('--upgrade',action='store_true',help='Upgrade a managed installation after draining all callers')
    p.add_argument('--prefix',type=Path,default=Path.home()/'.local/share/simulator-manager')
    p.add_argument('--bin-dir',type=Path,default=Path.home()/'.local/bin')
    # Honor an existing managed legacy Skill rather than creating a duplicate.
    legacy=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))/'skills'
    default_skills=legacy if (legacy/'simulator-manager/.simulator-manager-install').is_file() else Path.home()/'.agents/skills'
    p.add_argument('--skill-dir',type=Path,default=default_skills)
    p.add_argument('--state-dir',type=Path,default=Path(os.environ.get('SIM_MANAGER_STATE_DIR',str(Path.home()/'Library/Application Support/simulator-manager'))))
    a=p.parse_args()
    os.umask(0o077)
    root=Path(__file__).resolve().parent
    sys.path.insert(0,str(root))
    from sim_manager import __version__
    prefix,binary,skill_root,state=(v.expanduser().resolve() for v in (a.prefix,a.bin_dir,a.skill_dir,a.state_dir))
    state.mkdir(parents=True,exist_ok=True,mode=0o700)
    if state.stat().st_uid != os.getuid():
        raise SystemExit('Shared state must belong to this user')
    state.chmod(0o700)
    with (state/'activation.lock').open('a') as lock:
        deadline=time.monotonic()+45
        while True:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic()>=deadline:
                    raise SystemExit('Another session is still setting up; retry bootstrap shortly')
                time.sleep(.1)
        target=binary/'sim-manager'
        marker=prefix/'.simulator-manager-install'
        installed=False
        if prefix.exists():
            if not marker.is_file():
                raise SystemExit('Existing prefix is not a managed installation; choose a different prefix')
            probe=subprocess.run([str(prefix/'bin/sim-manager'),'--version'],capture_output=True,text=True)
            if probe.returncode or probe.stdout.strip()!='sim-manager '+__version__:
                if not a.upgrade:
                    raise SystemExit('Managed installation needs an update. Drain all work, pause new callers, then run bootstrap.sh --upgrade.')
                needs_install=True
            else:
                needs_install=False
        else:
            needs_install=True
        if needs_install:
            args=[sys.executable,str(root/'install.py'),'--prefix',str(prefix),'--bin-dir',str(binary),
                  '--skill-dir',str(skill_root),'--state-dir',str(state)]
            if marker.is_file():
                args.append('--upgrade')
            result=subprocess.run(args,capture_output=True,text=True)
            if result.returncode:
                raise SystemExit(result.stderr.strip() or result.stdout.strip())
            installed=True
        # Repair only a missing link / missing Skill; never replace unrelated content.
        if target.exists() or target.is_symlink():
            if not target.is_symlink() or target.resolve()!=prefix/'bin/sim-manager':
                raise SystemExit('CLI path is occupied by an unrelated executable')
        else:
            binary.mkdir(parents=True,exist_ok=True)
            target.symlink_to(prefix/'bin/sim-manager')
        skill=skill_root/'simulator-manager'
        if skill.exists() and not (skill/'.simulator-manager-install').is_file():
            raise SystemExit('Existing Skill is unmanaged; refusing to overwrite it')
        if not skill.exists():
            shutil.copytree(root/'skill/simulator-manager',skill)
            (skill/'.simulator-manager-install').write_text('1\n')
        (skill/'references/installation.md').write_text(
            'Installed CLI: `'+str(target)+'`\n\nShared state: `'+str(state)+'`\n\n'+
            'Use this same state for all sessions. Pass `--state-dir` explicitly if it is custom.\n')
        args=[str(target),'enable','--state-dir',str(state),'--project',a.project,'--json']
        if a.session:args.extend(['--session',a.session])
        if a.owner_pid:args.extend(['--owner-pid',str(a.owner_pid)])
        if not a.no_prepare:args.append('--prepare')
        result=subprocess.run(args,capture_output=True,text=True)
        if result.returncode:
            raise SystemExit(result.stdout.strip() or result.stderr.strip())
        response=json.loads(result.stdout)
        response.update(cli=str(target),skill=str(skill/'SKILL.md'),installed=installed,version=__version__)
        print(json.dumps(response,ensure_ascii=False))


if __name__=='__main__':
    main()
