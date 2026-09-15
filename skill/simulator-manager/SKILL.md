---
name: simulator-manager
description: Coordinate shared macOS iOS Simulators, Android Emulators, and GUI test resources across Codex sessions. Use before mobile runtime, UI, screenshot, gesture, or instrumented testing; acquire a dedicated resource through the shared FIFO manager and release it afterward.
---

# Simulator manager

Use `sim-manager` for shared runtime resources. If it is absent from PATH, read [references/installation.md](references/installation.md) for the installed executable location. Read [references/usage.md](references/usage.md) for manual leases, configuration, and recovery.

## Enable this session

When the user asks to enable shared mode, use the repository procedure at
`https://github.com/HankHuang0516/simulator-manager/blob/main/SESSION_START.md`.
For an installed manager, run `sim-manager enable --session ACTUAL_TASK_ID --prepare --json`.
Save the returned session label and shared state path for subsequent calls.
If no actual task ID is available, let the CLI generate a label and keep it.
Session registration records intent; each runtime reservation still uses a
supervised process lease. Bootstrap installs the complete CLI once, not only
this Skill. Missing SDKs do not disable coordination: report platform readiness
and keep builds outside reservations.

## Decide whether runtime is required

First perform applicable static checks, builds, and host unit tests. Documentation, pure models, and compile-only changes generally do not need a simulator. Android JVM tests run without an emulator. iOS host/package tests may run without a simulator, but application XCTest targets that require a simulator **must acquire before their test run**. Do not avoid required runtime tests merely because the unit/build phase passed.

Acquire for UI/layout, navigation, gestures, animation, app lifecycle, platform runtime behavior, screenshots, and device/instrumented tests. Keep compilation outside the lease where practical; build-for-testing first and test-without-building inside a lease when supported.

## Preferred lifecycle: one supervised command

Use a session label from the actual task ID if available, otherwise a unique project/task label. Labels are for attribution; the random lease token grants access. All sessions must use the same state directory and configuration.

```sh
sim-manager run ios --session '<actual-task-id>' --boot --timeout 300 --command-timeout 600 -- sh -c '
  set -eu
  xcodebuild test-without-building -scheme MyApp -destination "id=$SIM_MANAGER_UDID"
'
```

```sh
sim-manager run android --session '<actual-task-id>' --boot --timeout 300 --command-timeout 600 -- sh -c '
  set -eu
  adb -s "$SIM_MANAGER_SERIAL" install -r app/build/outputs/apk/debug/app-debug.apk
  adb -s "$SIM_MANAGER_SERIAL" shell am start -n com.example.app/.MainActivity
'
```

Use `set -eu` inside a multi-step shell script so a failed intermediate step fails the task. Substitute project-specific identifiers and run meaningful assertions. `run` waits FIFO, renews its lease, registers the workload process group, forwards interruption by cancelling that group, and releases on success/failure. It waits for surviving descendants too. Do not detach test subprocesses into new sessions or daemons.

Only use the assigned `SIM_MANAGER_UDID` / `SIM_MANAGER_SERIAL`. Never use `booted`, an implicit adb target, a random device, `shutdown all`, `erase all`, `adb kill-server`, or stop another session's simulator. The manager deliberately has no shutdown command; release only frees the reservation and leaves the runtime running for reuse. Boot goes through `run --boot` or `sim-manager boot TOKEN`.

## Multi-step interactive work

Prefer one script under `run`. For GUI tools that must span separate tool calls, acquire with an explicitly verified **long-lived owner PID**, preserve the token in the current session, and renew before expiry. The default parent of a short-lived tool shell may exit immediately and is not a valid durable session owner. Never invent a PID or use PID 1. Set a trap when shell work can fit in one invocation; use release in `finally` for other orchestrators.

When controlling the foreground desktop with GUI automation, configure `global_capacity: 1` for the shared manager so iOS, Android, and generic GUI work serialize. A generic `gui` lease does not magically coordinate with an already-held mobile lease; avoid nested acquisitions. For more parallel headless/device-targeted testing, use dedicated devices and a larger shared budget. Keep nested acquisitions out of workflows to avoid deadlocks.

On failure, release your token. If `release` refuses because your work is alive, cancel/finish that work first. On queue timeout, report the unavailable runtime validation; do not bypass the queue or silently claim UI tests passed. `cleanup` only reclaims proven-dead owners/work, and does not grant permission to stop devices. If config changes are pending, drain leases and queue before changing capacity/device assignments.

## Scope and enforcement

This is cooperative coordination among sessions using this Skill/CLI, not OS enforcement against other apps or agents. Skill discovery can be implicit, but existing sessions must load it or follow the project's `AGENTS.md` rules. Use the provided rules snippet to make coordination a project convention. Refuse an unknown already-running device by default; `allow_attach` is only for a pool explicitly reserved for the manager, never a workaround for another user's/session's active device.
