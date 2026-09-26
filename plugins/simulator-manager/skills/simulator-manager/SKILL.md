---
name: simulator-manager
description: Coordinate macOS iOS Simulators, Android Emulators and GUI resources across Codex tasks through a warm shared FIFO pool, atomic multiplayer device groups, bounded use, safe yielding and automatic release.
---

# Simulator Manager

Use the official `simulator_manager_*` MCP tools when available. The `sim-manager` CLI is the fallback. Read [SESSION_START.md](../../../SESSION_START.md) for one-sentence activation and [references/installation.md](references/installation.md) if the CLI is missing. All tasks on the Mac account must use the same local state and configuration. Coordination is cooperative; registration alone does not prove that a task followed the rules.

## Enable the current task

Call `simulator_manager_enable` with the actual task ID and canonical absolute project path. CLI equivalent:

```sh
sim-manager enable --session '<actual-task-id>' --project '/absolute/project/path' --prepare --json
```

Keep that session/project pair on every later call, even from a different working directory. Do not invent a new label per request or a long-lived owner PID. After enabling, and before each runtime/UI phase, call `simulator_manager_guidance` (CLI: `sim-manager audit --session '<actual-task-id>' --json`). Explain an active, reliably attributed finding to the task before acknowledging it. Do not infer a violator from an unregistered or overlapping process tree.

New installations use the **warm shared pool** (`traditional` mode). A bounded set of manager-owned devices is lent to tasks in FIFO order. Release returns use rights and leaves the device booted and its apps/data intact for the next borrower. A saved older installation may still use private Dynamic mode until an idle-window migration; never rewrite its config while leases or waiters exist.

## Request a simulator only when needed

Run applicable static checks, builds and host unit tests first. Documentation, pure models, ordinary JVM tests and compile-only work generally need no simulator. Device-dependent XCTest, Android instrumentation, runtime behavior, UI/layout, navigation, gestures, lifecycle and screenshots require a managed lease. Build-for-testing before occupancy and test-without-building during occupancy when supported. Do not claim runtime/UI verification from a build alone.

Prefer one supervised `simulator_manager_run` call with `platform`, saved `session`, canonical `project`, and an argv `command`; leave `boot: true` for mobile checks. CLI examples:

```sh
sim-manager run ios --session '<task-id>' --project '/absolute/project/path' --boot -- \
  sh -eu -c 'xcodebuild test-without-building -scheme MyApp -destination "id=$SIM_MANAGER_UDID"'

sim-manager run android --session '<task-id>' --project '/absolute/project/path' --boot -- \
  sh -eu -c 'adb -s "$SIM_MANAGER_SERIAL" install -r app-debug.apk; ./targeted-device-check "$SIM_MANAGER_SERIAL"'
```

For visible desktop automation add `--foreground`; it serializes the single GUI lane. Never acquire a nested `gui` lease while holding a foreground mobile lease. Use only the assigned UDID/serial: no `booted`, implicit adb target, arbitrary simulator selection, `shutdown all`, `erase all`, `adb kill-server`, or another task's device.

## Multiplayer: acquire a whole device group

If a test requires several devices simultaneously, request them **as one atomic same-platform group**. In the MCP tool set `device_count: N`; in the CLI use `--count N`. Do not acquire devices one by one or keep a partial set while waiting. The group waits at one FIFO position until all N devices and capacity are available. It receives one shared occupancy/yield policy; every lease is tracked during the same workload and all are released on success, failure, interruption or timeout.

An operator prepares enough manager-owned shared devices during a drained window with `sim-manager setup ios --count N` or `sim-manager setup android --count N`. If capacity is insufficient, report it and wait for that setup; never use an unmanaged device to fill the gap. Example:

```sh
sim-manager run android --count 2 --session '<task-id>' --project '/absolute/project/path' --boot -- \
  sh -eu -c './multiplayer-check "$SIM_MANAGER_SERIALS"'
```

`SIM_MANAGER_SERIALS` and `SIM_MANAGER_UDIDS` are comma-separated ordered lists; `SIM_MANAGER_RESOURCE_IDS` and `SIM_MANAGER_TOKENS` use the same order. `SIM_MANAGER_COUNT` is the granted size. Singular variables identify the first device for compatibility. The test must parse the list and explicitly target **every** participant (`adb -s SERIAL`, `xcodebuild -destination id=UDID`). Cross-platform iOS+Android groups are not atomic yet; use separate bounded phases. Shared devices retain prior apps/data by design, so use separate test accounts, namespaces and records when different tasks or players use the same app. Never erase another task's data as generic cleanup.

## Occupancy, yielding and release

- Total budget is at most **2400 seconds**, including grant, boot, installation, test and export. Request less with `--budget-seconds`. Booting devices sequentially does not reset the clock. No renewal may extend the original hard deadline.
- At most **3 actual lease extensions**. If another task waits, finish or checkpoint at the **120-second** fair-use boundary. If already past it, use the bounded **10-second** checkpoint window. Yield and rejoin at the FIFO tail for remaining validation.
- Exit **75** signals safe yielding. `--requeue-on-yield` is only for explicitly checkpointed/restartable commands; an arbitrary test is never automatically replayed safely. Exit **124** means the budget or phase timeout ended.
- Always finish/cancel the owned workload and release every lease. Supervised `run` tracks its complete process group and releases in a `finally` path. Do not daemonize or escape that group. A live uninterruptible descendant keeps its reservation until it exits.
- **Release never means power off.** Do not run `simctl shutdown`, close an emulator, use `adb emu kill`, or add shutdown actions to success/failure cleanup. Only Simulator Manager may retire an unleased, provenance-verified device for real capacity, critical pressure or configured idle maintenance. Direct shutdown actions are rejected and may appear in compliance coaching.

Manual `acquire` needs a verified long-lived owner PID and explicit `finally` release; the CLI's `--shell` exports one token for a single device or `SIM_MANAGER_TOKENS` for a group. A manual live-owner lease cannot be safely inferred complete, and strict deadline enforcement requires supervised `run`. Never kill a Codex task owner PID to reclaim a slot.

## Pressure, recovery and visibility

The watcher samples host memory pressure, normalized load and disk. In optional Dynamic mode, sustained pressure progresses Dynamic → Constrained → Draining → Traditional, with slower recovery. Active leases are not evicted. A private environment is never lent to another task; private idle retirement is manager-only. In default shared mode, bounded pool/global capacities and FIFO remain authoritative. Do not edit caps to bypass pressure.

Use `sim-manager status --json` to inspect queue, owners, deadlines, renewals, resources, environments and host state. `cleanup --json` safely reaps proven-stale state; unknown external or ambiguous activity stays protected. The floating `sim-manager ui` dashboard is a single Dock-visible instance. Its Monitor button performs a read-only bypass audit; a clear result covers only uniquely attributable registered task trees. The dashboard follows device language by default and allows English/Traditional Chinese selection. Registration is not runtime/UI acceptance.

To migrate an older private installation, first verify zero leases and FIFO waiters. Prepare one shared device per platform if missing, run `switch-shared`, then use `prune-private` only after confirming every private device is offline and provenance-verified. The cleanup reports ambiguous leftovers instead of guessing or touching external devices. After recovering disk, prepare extra shared devices for actual multiplayer demand with `setup ios|android --count N`. Do not run setup, switching or pruning during active work.

For Unity-based Android work, read [references/unity.md](references/unity.md). Unity may enumerate devices and stop shared ADB indirectly; do not diagnose it by restarting the Editor/device or exporting full preferences/environment error blocks. Keep the existing REBOUND Android retry pause until the shared ADB remote-stop cause is attributed and explicitly resumed.
