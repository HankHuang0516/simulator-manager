# Enable this session

This is the agent procedure for the user request:

> Use the simulator-manager Skill from https://github.com/HankHuang0516/simulator-manager and enable shared mode for this session.

1. Read `skill/simulator-manager/SKILL.md` from this repository. Adopt its rules for the current session, including builds/host tests first, explicit device targets, supervised runtime work, and release on completion/failure.
2. If the Simulator Manager Tool is available, call `simulator_manager_enable` with the actual task label and canonical project path. Otherwise use a managed `sim-manager` version 4.0.0 and run `enable --session ACTUAL_SESSION_LABEL --project ACTUAL_PROJECT_PATH --prepare --json`. Read its installed `references/installation.md` to preserve the shared state path. Use the same custom `--state-dir` when one was previously configured. Do not create a per-project state directory.
3. Otherwise clone this repository into a newly created temporary directory, then execute `bootstrap.sh --session ACTUAL_SESSION_LABEL --project ACTUAL_PROJECT_PATH`. Do not overwrite an existing checkout. Bootstrap installs once, serializes concurrent installation, reuses managed files, and preserves the pool config. It defaults to the documented user Skill directory `~/.agents/skills`, or reuses a managed legacy installation.
4. Save the returned session label, canonical absolute project path, CLI path, and state path in the current session context. If there is no actual task ID available, let bootstrap generate the label and use that returned label consistently. A registration label is attribution, not a process lease. Do not invent an owner PID.
5. Read `mode`, `platforms`, and `watcher` in the response. New installations default to the warm shared FIFO pool (`traditional`). Existing installations retain their saved mode until an idle-window migration. `setup ios|android --count N` can prepare a bounded group for multiplayer tests after leases and waiters drain. The watcher samples host pressure and retires only unleased private VMs. Setup creates dedicated **shutdown** devices only from installed SDK components. It does not boot devices, download runtimes, adopt personal simulators, or overwrite a disabled/custom pool. If SDK components are missing or setup is deferred, shared mode is enabled but that platform is not ready; report the exact reason and retry setup when runtime validation is needed.
6. Immediately call `simulator_manager_guidance` for the saved session (CLI fallback: `sim-manager audit --session SAVED_LABEL --json`). Explain every active, unacknowledged finding in the current task, including why direct simulator use can overlap another task and the exact managed replacement. Acknowledge only after explaining it. Repeat this check before each runtime/UI phase.
7. Confirm shared mode is enabled with the session label and platform readiness. From this point, use `sim-manager run ios|android --session SAVED_LABEL --project SAVED_PROJECT_PATH --boot ... -- COMMAND` for runtime/UI work. A same-platform multiplayer test requests its whole device set with `--count N` (MCP: `device_count: N`), then targets every returned UDID/serial explicitly. The group waits and releases atomically; never grab devices one by one. Explicitly pass a custom shared state path on every call. Do not acquire for builds or host tests that do not need runtime. Use `--foreground` for visible GUI automation. Observe the total budget, renewal cap and safe-yield rules. On exit 75, finish/checkpoint and requeue remaining validation; automatic requeue is only for explicitly restartable commands. On every success, failure, timeout or interruption, release use rights but leave the simulator/emulator running. Never add `simctl shutdown`, `adb emu kill`, emulator-close actions or shutdown cleanup traps; only manager maintenance may retire a verified unleased device.

Example bootstrap after cloning (substitute real session/project values):

```sh
./bootstrap.sh --session 'actual-task-id' --project '/absolute/project/path'
```

Existing installed CLI:

```sh
~/.local/bin/sim-manager enable --session 'actual-task-id' --project '/absolute/project/path' --prepare --json
```

If an older managed version is installed, finish existing work and pause new callers before `bootstrap.sh --upgrade`. Do not replace installed code while other sessions are using it.

The user instruction authorizes this session to participate. The Skill and session registration do not enforce behavior in other sessions: each session must receive the same instruction or follow shared project rules. Keep the current session in this mode until the user changes that preference.

Every acquire/run must pass the saved `--project` even when the tool working directory changes. Missing `--project` defaults to the current directory, not the registered project. Keep `--mode auto`; group requests automatically select the shared pool. A shared device retains apps/data between borrowers, so tests must use distinct accounts or isolated records when they touch the same app.
