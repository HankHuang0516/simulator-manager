# Manual lease and recovery

`run` is the safe default for one-shot Codex shells. For a single durable shell:

```sh
set -eu
# $$ identifies this shell, which must stay alive throughout the lease.
lease_env=$(sim-manager acquire ios --owner-pid "$$" --session 'project-task' --shell --timeout 300)
eval "$lease_env"
trap 'sim-manager release "$SIM_MANAGER_TOKEN" >/dev/null' EXIT
trap 'exit 130' INT TERM HUP
sim-manager boot "$SIM_MANAGER_TOKEN" --timeout 180
# Run tests synchronously and explicitly target $SIM_MANAGER_UDID.
# For longer manual work, renew from this shell or its orchestrator.
sim-manager renew "$SIM_MANAGER_TOKEN" --lease-seconds 900 >/dev/null
```

Never `eval` human/JSON output. `--shell` uses shell-escaped exports. Tokens are private capabilities, not resource IDs or session labels; `status` never prints them. `--json` gives a single JSON object; errors have `error` and `code`. `run --json` sends child output to stderr to keep stdout machine-readable.

Manual lease TTL expiry prevents new boot/work API calls but does not transfer a device from a live owner. Renew or release. This deliberately avoids stealing a device from a suspended or slow owner. A manual caller must hold its owner alive until all untracked work finishes. Only supervised `run` tracks descendants; detached daemons, external GUI automation workers, and manually launched processes are outside that protection.

`status --json` shows owner PID/start, project, session, expiry, operation, ordered queue, and recent bounded audit events. `cleanup --json` removes dead queue waiters and reservations whose owner and registered activity have both exited. Every acquire/status/cleanup also performs this reclamation; no background daemon is required. A reboot invalidates old process identities. PID start timestamps and boot identity avoid ordinary PID reuse; if a process-group number is reused ambiguously, reservation is retained conservatively until that group ends or the host reboots.

The default directory is shared across projects for the same macOS account, not across different users or different hosts. It contains `config.json`, SQLite database/WAL files, and Android logs. Keep it on local storage, not NFS/iCloud-synced storage. Restrictive user permissions protect capabilities. Do not delete the database while workers are running.

Pool capacity limits simultaneous reservations (not the number of booted VMs); a global weighted budget bounds total reservations. FIFO is strict within a pool. Across pools, the oldest satisfiable pool head gets the available budget, so a blocked Android head does not idle an available iOS resource. Independent pools can progress concurrently. Avoid acquiring another resource while holding one.

Boot integrations never stop a VM. iOS uses only its pinned UDID; Android uses its pinned AVD and even console port. Boot failure releases the lease but may leave a manager-started runtime running; subsequent boot can recover it. If an externally started runtime is rejected, select a different dedicated device or finish that external work yourself. Do not change `allow_attach` to bypass an active owner.
