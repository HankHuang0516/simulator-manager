"""Gated process groups: register ownership before any user command can execute."""
import os
import signal
import subprocess
import sys
import time
from .core import boot_id, group_alive


def spawn_gated(command, env=None, stdout=None, stderr=None):
    read_fd, write_fd = os.pipe()
    try:
        child = subprocess.Popen([sys.executable, '-m', 'sim_manager.worker', str(read_fd), *command],
                                 pass_fds=(read_fd,), start_new_session=True, env=env,
                                 stdout=stdout, stderr=stderr)
    except BaseException:
        os.close(write_fd)
        raise
    finally:
        os.close(read_fd)
    return child, write_fd


def cancel_group(child, grace=3):
    machine_boot = boot_id()
    def send(sig):
        if not group_alive(child.pid, machine_boot):
            return
        try:
            os.killpg(child.pid, sig)
        except ProcessLookupError:
            pass
        except PermissionError:
            # Darwin can return EPERM for groups that have just become empty
            # or zombie-only. A genuinely live group remains protected.
            if group_alive(child.pid, machine_boot):
                raise
    send(signal.SIGTERM)
    end = time.monotonic() + grace
    while time.monotonic() < end:
        child.poll()
        if not group_alive(child.pid, machine_boot):
            break
        time.sleep(.05)
    send(signal.SIGKILL)
    try:
        child.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass  # Reservation stays protected if an uninterruptible worker survives.


def execute(manager, token, command, env=None, timeout=None, operation='work', stdout=None, stderr=None):
    child, gate = spawn_gated(command, env, stdout, stderr)
    registered = False
    try:
        manager.begin_activity(token, operation, child.pid, child.pid)
        registered = True
        os.write(gate, b'1')
        os.close(gate)
        gate = None
        end = time.monotonic() + timeout if timeout is not None else None
        renew_at = time.monotonic() + min(10, manager.config['lease_seconds']/3)
        while True:
            rc = child.poll()
            busy = group_alive(child.pid, manager.machine_boot)
            if rc is not None and not busy:
                return rc if rc >= 0 else 128-rc
            if end is not None and time.monotonic() >= end:
                cancel_group(child)
                return 124
            if time.monotonic() >= renew_at:
                manager.renew(token)
                renew_at = time.monotonic() + min(10, manager.config['lease_seconds']/3)
            time.sleep(.1)
    except BaseException:
        cancel_group(child)
        raise
    finally:
        if gate is not None:
            os.close(gate)
        # If descendants survive even SIGKILL (e.g. uninterruptible sleep),
        # retain activity and lease until a later cleanup proves they exited.
        if registered and not group_alive(child.pid, manager.machine_boot):
            manager.end_activity(token)
