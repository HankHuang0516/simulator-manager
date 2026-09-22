---
name: simulator-manager
description: Coordinate macOS iOS Simulators, Android Emulators and GUI test resources across Codex sessions. Default to a Dynamic Simulator Pool with private session environments, pressure-aware Traditional FIFO fallback, bounded occupancy, safe yielding and guaranteed release for runtime/UI testing.
---

# Simulator manager

Use the Simulator Manager MCP tools when this Skill is provided by the official Plugin. They wrap the complete `sim-manager` CLI and keep runtime work inside one supervised call. Use the CLI examples as a fallback or when the user explicitly requests shell usage. If the CLI is absent, read [references/installation.md](references/installation.md). Read [references/usage.md](references/usage.md) for manual leases, policy and recovery. Coordination is cooperative; every participating session must adopt it.

## Enable this session

Follow `https://github.com/HankHuang0516/simulator-manager/blob/main/SESSION_START.md`.
Use the one-command Tool installer or bootstrap to install the complete project once. With the Plugin active, call `simulator_manager_enable` using the actual task ID and canonical project path. CLI fallback:

```sh
sim-manager enable --session '<actual-task-id>' --project '/actual/project/path' --prepare --json
```

Save the session label, canonical absolute project path, CLI and shared state path for every later call. Pass both `--session` and `--project` explicitly on every acquire/run, including calls from other working directories. Registration does not supply a default project to subsequent requests; omitting `--project` uses the current working directory and may request a different environment. If no task ID is available, let bootstrap/enable generate a label and retain it. Do not generate a new label per tool call. All projects on the Mac account use the same state/config. Registration records intent; supervised leases enforce actual reservations. Missing SDK readiness does not permit bypassing coordination.

After enable, call `simulator_manager_guidance` for the saved session. Before every runtime/UI phase, call it again. If it returns an active finding, immediately explain in the current task that direct `simctl`, `adb`, `emulator`, or unleased simulator testing can overlap another task, then give the returned managed replacement. Acknowledge only after the explanation. The watcher stores only the action category and task attribution; it never stores full commands or terminates a task/device. A task that never registers cannot be safely attributed from an OS process alone, so treat any dashboard coverage gap as a request to enable that task, never as permission to guess or kill.

New installations use **Dynamic Simulator Pool**. Each session + project + platform has a fixed private device/AVD, created lazily within a reserved budget. Preserve the project path and label. This isolates device data, not the entire host or foreground GUI. Existing configs without `mode` preserve **Traditional Mode**, the original shared-pool FIFO scheduler. Do not change shared configuration during active work or to bypass pressure.

## Build and host tests first

Perform applicable static checks, builds and host unit tests before requesting a simulator. Documentation, pure models, JVM tests and compile-only changes generally need no simulator. Simulator-dependent application XCTest, runtime behavior, instrumented tests, UI/layout, navigation, gestures, lifecycle and screenshots require a lease. Build-for-testing outside occupancy and test-without-building inside it when supported. Never claim required UI/runtime checks passed solely because a build passed.

## Preferred lifecycle: one supervised Tool call

Call `simulator_manager_run` with `platform`, the saved `session`, the canonical `project`, and `command` as an argv array. Keep the default `boot: true` for mobile runtime checks. Set `foreground: true` only for visible desktop automation. The Tool uses Dynamic mode with pressure-aware fallback and guarantees release before returning.

The equivalent CLI form is:

```sh
sim-manager run ios --session '<saved-label>' --project '/saved/absolute/project/path' --boot --timeout 300 --budget-seconds 600 -- sh -eu -c '
  xcodebuild test-without-building -scheme MyApp -destination "id=$SIM_MANAGER_UDID"
'
```

```sh
sim-manager run android --session '<saved-label>' --project '/saved/absolute/project/path' --boot --timeout 300 --budget-seconds 600 -- sh -eu -c '
  adb -s "$SIM_MANAGER_SERIAL" install -r app/build/outputs/apk/debug/app-debug.apk
  adb -s "$SIM_MANAGER_SERIAL" shell am start -n com.example.app/.MainActivity
  # Add meaningful, explicitly targeted runtime assertions.
'
```

Substitute project identifiers. Use `sh -eu` for multi-step failure propagation. `run` reserves admission, counts creation + boot + work from the same grant, tracks the complete workload group, and releases on success/failure/interruption/timeout. Never daemonize tests or escape their process group.

For visible desktop automation, add **`--foreground`** to the mobile request. The manager atomically serializes foreground requests and the `gui` pool. Do not acquire a nested GUI lease while holding a simulator. Headless/device-targeted work can run in parallel private environments.

Only use assigned `SIM_MANAGER_UDID` / `SIM_MANAGER_SERIAL`. Never use `booted`, an implicit adb target, arbitrary devices, `shutdown all`, `erase all`, `adb kill-server`, or another session's device. Boot through `run --boot` or `boot TOKEN`. **Release the lease without powering off the simulator or emulator.** Release frees use rights while leaving a verified private VM warm for the next managed request and preserving session device data. Do not run `simctl shutdown`, `adb emu kill`, close an emulator to end a managed run, or add shutdown commands to cleanup traps. Only the manager's idle maintenance may stop its own provenance-verified **unleased private** VM by exact identifier for real capacity needs, critical pressure, or the configured idle timeout.

## Occupancy and fair-use rules

- Default total use budget: **600 seconds including creation and boot**. Request less with `--budget-seconds`; never exceed the shared maximum. Do not reset the clock by switching phases or renewing.
- At most **3 actual lease extensions**. No-op renewal checks do not count; every extension remains capped by the original hard deadline. An expired lease cannot be revived. Finish/cancel, release and request a fresh lease.
- When someone waits, yield at the **120-second** slice boundary. If that boundary is already past, the manager gives a **10-second** checkpoint window, capped by the original deadline. Observe the reported `yield_by` and remaining time.
- Split validation into short chunks. Handle SIGTERM by checkpointing and exiting; the supervisor then cancels surviving owned processes after its termination grace. Cancellation is not arbitrary side-effect rollback.
- Exit **75** means safely yielded: request remaining work again at the **FIFO tail**. Keep the same environment label, but obtain a new lease token. Never continue using the simulator between leases or jump the queue.
- Only explicitly checkpointed/restartable commands may use `--requeue-on-yield --max-requeues 3`. Otherwise save remaining steps and request them separately. If retries/queue wait end, report pending validation and requeue remaining work when continuing; never silently skip it.
- On total timeout (124), failures or interruption, ensure release. If registered descendants survive, finish/cancel your own work; do not force reclamation or stop the long-lived Codex owner.

## Pressure-aware fallback

The watcher samples host memory pressure, normalized load and disk. Sustained pressure progresses **Dynamic → Constrained (pause creation) → Draining (reduce admission) → Traditional**. Recovery is slower and gradual. Accept the current admission limit. Keep `--mode auto` for pressure-aware reuse of your assigned private environment. A reported Traditional stage is an admission state; explicitly requesting `--mode traditional` instead selects the static pool. Do not force new environments or change caps to evade it.

Downgrades preserve active leases and private assignments. Existing private environments can be reused serially under Traditional admission; new owners may receive configured Traditional fallback slots. Another session's private environment is never shared. Idle private VMs may be stopped while retaining data. Capacity/environment exhaustion means wait/timeout, not erase/reassign another environment.

## Manual and recovery boundaries

Prefer one `run` invocation. Multi-tool interactive leases require a verified long-lived owner PID, preserved token, reported deadline checks, synchronous work and release in `finally`. Never invent a PID, use PID 1 or assume a one-shot tool shell is a durable owner. Manual external GUI activity cannot be strictly supervised: expired live-owner reservations stay protected. Strict time enforcement requires `run`.

Use `status --json` to inspect mode, metrics, deadlines, owners, environments and queue. `cleanup --json` performs safe maintenance. The watcher reclaims dead-owner/dead-work reservations and cancels tracked orphan groups at their deadline; it never kills Codex session PIDs. If the watcher crashes, activation/dynamic runs restart it. Unknown external runtimes, partial creation and ambiguous shutdown are quarantined rather than adopted or stolen.

On queue timeout, report the exact unverified runtime checks and retry through the manager. Config changes/upgrades require drained leases/queues and paused callers. Do not enable `allow_attach` to bypass another owner. This mode remains the current session convention until the user changes it.

## Dashboard

When asked to view scheduling, run the installed CLI `sim-manager ui`. On macOS 13+ it lazily builds and opens a native floating SwiftUI panel using Xcode command line tools. While running, the app remains visible with its custom routing icon in the Dock and in the menu bar; either entry can reopen a hidden panel. It uses the same shared state, updates every two seconds, and has no resource control buttons. Recent Activity names the task/project and platform, and shows live elapsed time or completed total occupancy. The language control defaults to the device's preferred language and can persistently select Automatic, English, or Traditional Chinese. Viewing status does not require acquiring a simulator. Registered sessions are adoption records, not proof of live work. The manager watcher operates independently of the panel.

## Android target recovery

Version 2.0.1 corrects new AVD root targets to the installed image's integer major API, keeping Major.Minor image paths. Do not replace `android-0` with a decimal/unknown API. If boot reports an invalid root target on an existing private AVD, acquire a short valid manual lease with a verified durable owner, set release traps/finally, and run `repair-android-target TOKEN` before `boot TOKEN`. This command only repairs your assigned idle private manifest after verifying no VM/work/occupied ports and valid installed SDK image metadata. It changes no userdata, SDK files, signatures or safety settings and does not reset deadlines. Rejecting a busy/external/ambiguous environment is a blocker to resolve through its owner, not permission to stop it. No background migration or cross-session repair is allowed.

Warm reuse: matching pending requests protect their running private environment. A queue or degraded stage alone does not trigger shutdown. Normal session completion means release only; it does not mean power off. Maintenance may retire unleased VMs for actual capacity needs, critical telemetry or the configured idle timeout; retain data and let the manager control retirement. Never hold/renew a lease merely to keep a VM warm.

## Unity shared ADB compatibility

Unity can enumerate Android devices during Editor/build/host-test startup and terminate shared ADB automatically, even when your script has no server commands. Read [references/unity.md](references/unity.md) before Unity-based shared Android work. Ensure its automatic ADB termination controls are disabled in a coordinated Editor-idle window; do not switch SDK paths or restart a server to bypass failures. Use sanitized failure logs: Unity error blocks can dump sensitive process environment values.
