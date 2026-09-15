# simulator-manager

**Shared simulators. Calm sessions.**

A Codex Skill and macOS resource scheduler for iOS Simulators, Android Emulators, and limited GUI test resources. Multiple sessions share one local state directory, queue fairly, and release their own reservations. Python 3.9+, standard library only.

## Enable it with one message

Paste this into **each Codex session**:

> Use the simulator-manager Skill from https://github.com/HankHuang0516/simulator-manager and enable shared mode for this session.

The agent follows [SESSION_START.md](SESSION_START.md), installs the complete manager once if needed, registers the current session, and reuses the same shared state for every project. You do not need to run a separate shell command yourself.

On first activation, bootstrap can create new, dedicated **shutdown** iOS/Android devices from SDK components already installed on the Mac. It never adopts your personal devices or downloads runtimes. Missing SDKs are reported as platform readiness issues; the session can still participate in sharing and perform builds/host tests. All participating sessions must receive the instruction or follow the same project rules. Registration records intent; actual exclusion is enforced by cooperative CLI leases.

![simulator-manager flowchart](assets/flowchart.png)

[Editable SVG](assets/flowchart.svg) · [Accessible flowchart](docs/FLOWCHART.md) · [Complete Skill](skill/simulator-manager/SKILL.md)

## What happens next

1. Run applicable builds, static checks, and host unit tests first.
2. Acquire only when runtime/UI/device validation is required. Simulator-dependent application XCTest and Android instrumented tests also require a lease.
3. Wait in FIFO order for a dedicated iOS, Android, or generic resource.
4. Record the session, project, owner PID/start time, host boot identity, and workload group.
5. Boot only the assigned UDID/serial, run validation, and renew automatically.
6. Finish or cancel your own work, then release on success, failure, interruption, or timeout. Leave the managed VM running for reuse.

## Manual bootstrap

If you prefer a terminal:

```sh
git clone https://github.com/HankHuang0516/simulator-manager.git
cd simulator-manager
./bootstrap.sh --session my-session --project /absolute/project/path
```

Bootstrap is idempotent. Concurrent activations serialize through an OS-managed setup lock. Later activations reuse the installation and preserve the existing pool configuration.

```sh
# Already installed: enable another session.
~/.local/bin/sim-manager enable --session another-session --prepare --json

# Retry dedicated setup after installing SDK components or finishing active work.
~/.local/bin/sim-manager setup ios --json
~/.local/bin/sim-manager setup android --json

# Install without provisioning devices.
./bootstrap.sh --session my-session --no-prepare
```

Bootstrap defaults:

| Item | Location |
| --- | --- |
| CLI | `~/.local/bin/sim-manager` |
| Program | `~/.local/share/simulator-manager` |
| Skill | `~/.agents/skills/simulator-manager`, or an existing managed legacy Skill |
| Shared state/config | `~/Library/Application Support/simulator-manager` |

The current documented user Skill discovery directory is `~/.agents/skills`. Bootstrap reuses an existing managed Skill under `${CODEX_HOME:-~/.codex}/skills` to avoid duplicates. Skill discovery supports explicit and implicit activation; existing sessions should load the Skill when asked. [Official Skill documentation](https://learn.chatgpt.com/docs/build-skills)

`./install.sh` remains available for installation without session registration or automatic provisioning. Its legacy default Skill location is `${CODEX_HOME:-~/.codex}/skills`; pass `--skill-dir "$HOME/.agents/skills"` to choose the documented user directory. Neither installer modifies shell profiles. The installed Skill records the absolute CLI and shared state paths, so activation does not depend on a refreshed PATH.

Custom installation:

```sh
./bootstrap.sh --session my-session \
  --prefix /path/to/program --bin-dir /path/to/bin \
  --skill-dir /path/to/skills --state-dir /path/to/shared-state
```

Every session must use the same custom `--state-dir` or `SIM_MANAGER_STATE_DIR`. Do not create separate state per project. `--config` / `SIM_MANAGER_CONFIG` can select another shared config. Options may precede or follow a subcommand; the workload after `run` must follow `--`.

For a version upgrade, finish all leases/queues, pause new callers, and use `./bootstrap.sh --upgrade`. Bootstrap refuses to overwrite unmanaged programs/Skills or upgrade busy installations.

## Supervised runtime testing

The saved session label should be passed to every `run`:

```sh
# Build-for-testing first, outside the reservation where practical.
sim-manager run ios --session my-session --boot \
  --timeout 300 --command-timeout 600 -- sh -eu -c '
    xcodebuild test-without-building -scheme MyApp \
      -destination "id=$SIM_MANAGER_UDID"
  '
```

```sh
# JVM tests and APK builds first.
sim-manager run android --session my-session --boot \
  --timeout 300 --command-timeout 600 -- sh -eu -c '
    adb -s "$SIM_MANAGER_SERIAL" install -r app/build/outputs/apk/debug/app-debug.apk
    adb -s "$SIM_MANAGER_SERIAL" shell am start -n com.example.app/.MainActivity
    adb -s "$SIM_MANAGER_SERIAL" exec-out screencap -p > screenshot.png
  '
```

The Android example installs, starts, and captures an app. Add project-specific assertions or an explicitly targeted instrumentation runner. Some Gradle connected-test tasks enumerate all devices; do not assume `ANDROID_SERIAL` alone isolates them.

Generic resources use the same lifecycle:

```sh
sim-manager run gui --session my-session --command-timeout 300 -- ./your-gui-test-script
```

`run` injects `SIM_MANAGER_TOKEN`, `SIM_MANAGER_RESOURCE_ID`, `SIM_MANAGER_POOL`, `SIM_MANAGER_SESSION`, `SIM_MANAGER_UDID`, `SIM_MANAGER_SERIAL`, and `SIM_MANAGER_AVD`. Expand these inside the child command, after acquisition.

The new-install default shared budget is **1**, serializing foreground desktop activity across pools. Increase it for independent device-targeted/headless testing with dedicated devices. Do not nest reservations. A generic `gui` lease does not implicitly lock an already-held mobile lease.

## Resource pool configuration

Automatic setup only fills empty mobile pools. It preserves custom, disabled, or intentionally paused pools. iOS setup creates a unique device using an available installed iOS runtime and compatible iPhone type. Android setup requires `adb`, `emulator`, `avdmanager`, and an installed host-compatible system image; it creates a unique AVD under the shared state's `avds/` directory. Setup does not boot either device. If preparation would require changing config while another session holds a lease/queue, it is deferred.

You can also configure devices manually. Use only devices explicitly reserved for the manager:

```sh
sim-manager discover ios --json
sim-manager discover android --json
```

See [config/example.json](config/example.json) for two iOS/two Android slots. Replace placeholder UDIDs/AVDs and set `enabled: true` for selected resources. Duplicate IDs, UDIDs, AVDs, and ports across pools are rejected.

```json
{
  "version": 1,
  "global_capacity": 1,
  "lease_seconds": 900,
  "poll_seconds": 0.25,
  "pools": {
    "ios": {
      "capacity": 1,
      "resources": [{"id": "phone", "kind": "ios", "udid": "YOUR-DEDICATED-UDID"}]
    },
    "android": {
      "capacity": 1,
      "resources": [{"id": "pixel", "kind": "android", "avd": "Codex_Pixel", "port": 5556}]
    }
  }
}
```

| Setting | Meaning |
| --- | --- |
| `capacity` | Nonnegative integer reservation limit for a pool; 0 pauses it |
| `global_capacity` | Shared weighted reservation budget across all pools |
| Resource `cost` | Budget units consumed by a reservation; default 1 |
| `enabled` | Whether a slot is available for allocation; default true |
| `lease_seconds` | Lease validity; `run` renews it automatically |
| `poll_seconds` | Queue polling interval |
| `allow_attach` | Explicit attachment to externally started dedicated devices; default false |
| Android `avd_home` | Optional private AVD directory, used by provisioned resources |
| `android_sdk` | Optional SDK root for tools and installed-system-image lookup |
| `tools` | Optional executable paths for `xcrun`, `adb`, `emulator`, `avdmanager` |

Each Android slot needs a different writable AVD and even console port (5554–5682). A second slot cannot reuse the same AVD. Boot arguments stay pinned; arbitrary extra emulator arguments are rejected.

Tool discovery uses PATH, then the configured/standard Android SDK location (`ANDROID_SDK_ROOT`, `ANDROID_HOME`, or `~/Library/Android/sdk`). AVD manager discovery checks `cmdline-tools/latest/bin` and installed command-line tool versions.

Capacity bounds **reservations**, not the number of booted VMs. Released VMs remain running, so keep the pool itself within the Mac's memory budget. Configuration changes apply when leases and queues have drained. `status` reports `config_pending` while busy; inconsistent new acquisitions fail. Release/status/cleanup can recover using the saved DB config if the config file is malformed or missing.

## CLI and output

```sh
sim-manager --version
sim-manager enable --session task-id --prepare --json
sim-manager setup all --json
sim-manager acquire ios --owner-pid "$LONG_LIVED_OWNER_PID" --session task-id --json
sim-manager acquire android --owner-pid "$LONG_LIVED_OWNER_PID" --shell --timeout 300
sim-manager boot "$SIM_MANAGER_TOKEN" --timeout 180 --json
sim-manager renew "$SIM_MANAGER_TOKEN" --lease-seconds 900 --json
sim-manager release "$SIM_MANAGER_TOKEN" --json
sim-manager status --json
sim-manager cleanup --json
sim-manager validate-config --json
```

`--json` produces one JSON object on stdout. `run --json` sends child stdout to stderr and returns `resource_id`, `exit_code`, and `released`. Human-readable output is formatted JSON; ordinary `run` preserves child output. `--shell` emits safely quoted exports for acquisition and renewal. Errors include `error` and `code` when JSON output is selected. `setup` reports readiness for each platform; SDK absence is a readiness result, not a claim of successful provisioning.

Tokens are private capabilities. Boot/renew/release use a token, never a resource ID or session label. `status` does not expose tokens. Release is retry-safe and refuses to free a resource while its registered workload is alive.

| Exit code | Meaning |
| --- | --- |
| 0 | Success |
| 1 | Configuration, SDK, or general error |
| 2 | CLI usage error |
| 3 | Queue timeout / no slot for try-once acquisition |
| 4 | Invalid token, owner, or active-work restriction |
| 124 | Supervised workload timeout |
| 130 | Manager interrupted |
| Other | Child exit code; signals become 128 + signal |

`--timeout 0` tries once without jumping the queue. For `run`, `--timeout` controls queue wait, `--boot-timeout` controls startup, and `--command-timeout` controls the workload.

### Manual multi-step leases

Prefer a single `run` script. A one-shot tool shell's parent may exit immediately, so a manual lease spanning multiple tool calls requires a verified long-lived owner PID. Do not invent one or use PID 1. Session registration is not a substitute for this owner.

```sh
set -eu
lease_env=$(sim-manager acquire ios --owner-pid "$$" --session my-session --shell)
eval "$lease_env"
trap 'sim-manager release "$SIM_MANAGER_TOKEN" >/dev/null' EXIT
trap 'exit 130' INT TERM HUP
sim-manager boot "$SIM_MANAGER_TOKEN"
# Run synchronously and explicitly target $SIM_MANAGER_UDID.
```

Only evaluate `--shell` output. Keep the owner alive until untracked manual work finishes, and renew before expiry. If release refuses, finish/cancel your work first. Do not daemonize or detach supervised workloads into another process group.

## Queue and crash recovery

SQLite `BEGIN IMMEDIATE` serializes allocation/release/cleanup. WAL and FULL synchronous preserve committed state. The OS/SQLite release transaction locks after a process crash; no stale file lock needs manual deletion.

FIFO is strict within each pool. Across pools, the oldest currently satisfiable pool head receives the shared budget; a blocked Android head does not idle an available iOS resource. Dead/expired waiters are removed automatically.

Leases record session/project, PID/start stamp, host boot identity, creation/expiry, and registered workload group. A pipe gate prevents the workload from executing until group registration commits.

- Owner dead and work ended: reclaim on the next acquire/status/cleanup.
- Owner killed but same-group children/grandchildren alive: keep the reservation until they finish.
- Supervisor dies before opening the gate: worker exits without executing the command.
- Host reboots: old process identities become invalid.
- TTL expires but owner is alive: block new boot/work calls, keep the reservation, allow renew/release. Time expiry never authorizes stealing live work.

The state directory is private (0700; files default 0600), local to one Mac account, and shared across its projects. Do not use NFS, cloud-synced storage, multiple hosts, or delete a busy DB. Audit history is bounded to approximately 1,000 events; status shows the latest 30. Android logs are retained under `logs/`; rotate them when work has drained. Session records show declared participation/last use, not an OS-enforced policy.

Process-group escape, external GUI workers, and SDK calls bypassing this Skill are outside cooperative protection. PID start stamps are second-resolution; rare ambiguous PID/group reuse conservatively delays reclamation. Uninterruptible surviving work keeps its reservation. A rare crash during device creation may leave a dedicated shutdown device before its config entry is committed; it is never auto-adopted by name.

## SDK integration and scope

iOS uses `xcrun simctl list`, explicit `boot UDID`, and `bootstatus UDID`. It does not bring Simulator.app forward automatically or use implicit `booted` targets. Android pins the AVD/port, verifies the AVD at the serial, checks port pairs, and waits for `sys.boot_completed=1`; all device adb commands use `-s SERIAL`.

No shutdown, erase, emulator-kill, or `adb kill-server` command is implemented. Existing unknown running devices are refused unless explicitly configured for attachment to an exclusive pool. `adb devices` can start the normal adb server. External SDK callers can still race with cooperative checks, so dedicated devices are essential.

[Apple CLI reference](https://developer.apple.com/documentation/xcode/xcode-command-line-tool-reference) · [Android emulator](https://developer.android.com/studio/run/emulator-commandline) · [ADB](https://developer.android.com/tools/adb) · [avdmanager](https://developer.android.com/tools/avdmanager) · [Android environment variables](https://developer.android.com/tools/variables)

## Project rules, tests, and validation

Merge [AGENTS.example.md](AGENTS.example.md) into project rules if every session should coordinate by default. Keep the Skill discoverable and use the one-message activation instruction for existing sessions.

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q sim_manager tests activate.py install.py
```

Tests use temporary shared state and fake SDK executables; they never boot/shutdown real devices. They cover concurrent/idempotent activation, config preservation, dedicated provisioning, missing SDKs, multi-process FIFO/exclusion/capacity, timeout, shell/JSON output, token isolation, owner/reboot invalidation, SIGTERM/SIGKILL/descendants, gate EOF, boot errors, and external-device refusal.

See [VALIDATION.md](VALIDATION.md) for the actual macOS verification and remaining live-SDK boundaries. [config/demo.json](config/demo.json) provides a generic single-slot pool for a no-SDK queue demonstration.

MIT licensed.
