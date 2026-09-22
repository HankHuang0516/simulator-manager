# Policy, manual leases and recovery

`run` is preferred: one total clock includes device creation, boot and validation. Default hard use budget 600 seconds, at most 3 actual extensions, waiter slice 120 seconds and checkpoint window 10 seconds. Renew cannot revive expiry or extend hard/yield deadlines. `--budget-seconds` can shorten the request. `--foreground` serializes desktop automation without nested GUI leases.

Exit 75 requires finishing/checkpointing, release and a fresh FIFO-tail request for remaining steps. `--requeue-on-yield` is only for explicitly restartable commands; arbitrary SDK side effects cannot be rolled back automatically. Cancellation sends SIGTERM then SIGKILL to the registered owned group, never the session owner. Surviving uninterruptible work stays reserved until confirmed dead.

```sh
set -eu
lease_env=$(sim-manager acquire ios --owner-pid "$$" --session 'saved-session-label' --project '/saved/absolute/project/path' --shell)
eval "$lease_env"
trap 'sim-manager release "$SIM_MANAGER_TOKEN" >/dev/null' EXIT
trap 'exit 130' INT TERM HUP
sim-manager boot "$SIM_MANAGER_TOKEN"
# Work synchronously, use explicit targets, finish before reported deadlines.
sim-manager renew "$SIM_MANAGER_TOKEN" --lease-seconds 120 --json
```

Only evaluate shell output. JSON output is one object; child output goes to stderr under `run --json`. Tokens are private capability credentials, never resource IDs/session names. Status omits tokens. A manual owner must remain alive and release in finally/trap; unknown external GUI work cannot be automatically judged complete. Expired live-owner manual leases are protected and cannot be renewed. Strict enforcement requires supervised run.

New installs use Dynamic Simulator Pool: fixed private environment per saved label + project + platform, lazily created with capacity reserved before SDK work. Unique Android AVD, writable home and reserved port; unique iOS UDID. This is device-data isolation, not host/container isolation. Pass the saved label and canonical absolute `--project` explicitly on every acquire/run. Omitting the project uses the current directory rather than session registration. Keep `--mode auto` to reuse private assignments during pressure fallback; explicit `--mode traditional` selects static resources. Private environment data persists across leases/idle shutdown; it is never lent to another session. `max_environments` bounds persistent assignment count; failed creation remains quarantined.

Pressure steps through Dynamic, Constrained, Draining and Traditional. Traditional is the original static FIFO mode; old configs without mode retain it. Do not override pressure or change shared config during work. Stage reductions affect new admissions; active work finishes/yields safely. The watcher retires at most one idle private VM per sample, only with exact identity/provenance and a transactionally reserved stopping row. Release itself does not shut down a VM. Static fallback slots remain running for reuse.

`status --json` includes scheduler metrics/stage, policy, owner/deadlines/renewals, environments, ordered queue and audit events. `cleanup --json` reaps proven-dead work and performs one maintenance tick. `watch --once` manually samples/maintains; `watch --stop` stops only the verified managed watcher. Activation/dynamic run starts one flock-protected watcher unless `monitor.daemon: false`. A dead supervisor with live same-group work stays reserved until completion/deadline; watcher cancels only the tracked group. A host reboot invalidates process/runtime identities. Partial creation and ambiguous PID reuse fail closed; interrupted idle shutdown is retried only after the stopper dies and provenance is rechecked.

One macOS account, one shared local state directory: `~/Library/Application Support/simulator-manager`. Use the same custom state for every project/session. Keep SQLite/WAL, logs, private AVD homes and locks on local storage. Never delete the database while work runs. Builds do not need reservations unless runtime-dependent. No implicit `booted`, untargeted adb, shutdown-all, erase or kill-server operations are allowed.

Warm reuse: matching pending requests protect their running private environment. A queue or degraded stage alone does not trigger shutdown. Every session releases its lease without powering off the assigned simulator/emulator, on success, failure, timeout and interruption. Never put `simctl shutdown`, `adb emu kill`, emulator-close actions or shutdown-all commands in normal completion, error handling, `finally`, traps or cleanup paths. Supervised `run` rejects explicit shutdown actions before admission; compliance coaching flags detected attempts even with a valid lease. Maintenance alone may retire a provenance-verified unleased VM for actual capacity needs, critical telemetry or the configured idle timeout; retain data and let the manager control retirement. Never hold/renew a lease merely to keep a VM warm.

Android transport readiness: version 2.0.3 performs bounded read-only retries within the original boot/lease deadline when exiting transports or occupied ports are still settling. It requires every emulator identity and a rechecked inventory before attachment or launch. Persistent unknown transports, duplicate AVDs or foreign ownership block work; do not bypass them, restart shared ADB or repeatedly reinstall. A later successful boot does not prove long-term daemon stability.
