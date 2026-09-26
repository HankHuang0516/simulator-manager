"""Gated process groups: register ownership before any user command can execute."""
import os
import re
import signal
import subprocess
import sys
import time
from .core import boot_id, group_alive, OwnershipError


_FORBIDDEN_RUNTIME_SHUTDOWN = (
    (re.compile(r'\bsimctl\b[^\n;&|]*\bshutdown\b', re.IGNORECASE),
     'simctl shutdown'),
    (re.compile(r'\badb\b[^\n;&|]*\bemu\s+kill\b', re.IGNORECASE),
     'adb emu kill'),
)


def validate_runtime_command(command):
    """Reject explicit device power-off actions before a lease is acquired.

    Release and power state are deliberately separate.  The manager may retire
    a provenance-verified unleased private runtime during maintenance; a task
    command must only finish its validation and return its use rights.
    """
    rendered = ' '.join(str(value) for value in command)
    for pattern, action in _FORBIDDEN_RUNTIME_SHUTDOWN:
        if pattern.search(rendered):
            raise ValueError(
                f'Runtime command contains forbidden `{action}`. Finish the test and '
                'release the lease; Simulator Manager keeps the device warm and alone '
                'decides when an unleased runtime may be retired.'
            )


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
    validate_runtime_command(command)
    budget = manager.budget(token)
    if budget['remaining_seconds']<=0:
        return 75 if budget['reason']=='requeue-required' else 124
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
            from .monitor import update
            update(manager)
            budget = manager.budget(token)
            rc = child.poll()
            busy = group_alive(child.pid, manager.machine_boot)
            if budget['remaining_seconds']<=0:
                cancel_group(child)
                return 75 if budget['reason']=='requeue-required' else 124
            if rc is not None and not busy:
                return rc if rc >= 0 else 128-rc
            if end is not None and time.monotonic() >= end:
                cancel_group(child)
                return 124
            if time.monotonic() >= renew_at:
                try:
                    manager.renew(token)
                except OwnershipError:
                    pass  # Cannot extend; continue only within the persisted deadline.
                renew_at = time.monotonic() + min(10, manager.config['lease_seconds']/3)
            time.sleep(.1)
    except OwnershipError:
        cancel_group(child)
        budget = manager.budget(token)
        if budget['remaining_seconds']<=0:
            return 75 if budget['reason']=='requeue-required' else 124
        raise
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


def execute_group(manager, tokens, command, env=None, timeout=None, stdout=None, stderr=None):
    """Run one gated workload while every device lease tracks its process group."""
    if not tokens:
        raise ValueError('A device group needs at least one lease')
    validate_runtime_command(command)
    budgets = [manager.budget(token) for token in tokens]
    exhausted = [b for b in budgets if b['remaining_seconds'] <= 0]
    if exhausted:
        return 75 if any(b['reason'] == 'requeue-required' for b in exhausted) else 124
    child, gate = spawn_gated(command, env, stdout, stderr)
    registered = []
    try:
        for token in tokens:
            manager.begin_activity(token, 'work', child.pid, child.pid)
            registered.append(token)
        os.write(gate, b'1')
        os.close(gate)
        gate = None
        end = time.monotonic() + timeout if timeout is not None else None
        renew_at = time.monotonic() + min(10, manager.config['lease_seconds']/3)
        while True:
            from .monitor import update
            update(manager)
            budgets = [manager.budget(token) for token in tokens]
            rc = child.poll()
            busy = group_alive(child.pid, manager.machine_boot)
            exhausted = [b for b in budgets if b['remaining_seconds'] <= 0]
            if exhausted:
                cancel_group(child)
                return 75 if any(b['reason'] == 'requeue-required' for b in exhausted) else 124
            if rc is not None and not busy:
                return rc if rc >= 0 else 128-rc
            if end is not None and time.monotonic() >= end:
                cancel_group(child)
                return 124
            if time.monotonic() >= renew_at:
                for token in tokens:
                    try:
                        manager.renew(token)
                    except OwnershipError:
                        pass
                renew_at = time.monotonic() + min(10, manager.config['lease_seconds']/3)
            time.sleep(.1)
    except BaseException:
        cancel_group(child)
        raise
    finally:
        if gate is not None:
            os.close(gate)
        if registered and not group_alive(child.pid, manager.machine_boot):
            for token in registered:
                manager.end_activity(token)
