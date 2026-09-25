# Validation report

Validated through September 25, 2026. Current version 3.6.1; earlier sections preserve historical validation evidence.

## Version 3.6.1 — shared-owner attribution guard

The monitor now compares all registered task process trees before attributing a simulator command. If two labels share the same host process or overlapping ancestry, activity in their common subtree becomes an explicit coverage gap rather than a task-specific violation. This closes a live false-attribution case where one task label held an Android lease and another label sharing its process root was reported for direct ADB use. Task-specific audits also leave other tasks' active findings intact. Two behavioral regressions cover both cases. The dashboard displays fully attributable registrations separately from unobservable and overlapping trees, and uses a neutral indicator whenever coverage is incomplete. All 118 automated tests pass locally, both Skill copies validate, and the native macOS 13 SwiftUI target compiles.

## Version 3.6.0 — one-click bypass monitor

The native dashboard now exposes a visible **Monitor** button that runs the existing compliance audit as a read-only inspection. Its report separates active findings that can be reliably attributed to a registered, observable Codex task from registration or process-observability gaps. A clear result therefore means no attributable bypass was observed inside the covered task trees; the UI explicitly avoids claiming that the entire Mac is free of unmanaged activity. Findings identify the task, project, platform and action category, then provide the managed replacement and warm-release guidance. The monitor never stops a task, simulator, emulator or ADB server.

The macOS 13 SwiftUI target compiles successfully, and a native screenshot confirmed that the localized Monitor control fits the dashboard header without colliding with the language, guide, pin or close controls. Automated coverage includes the UI command contract and safety copy plus an unobservable-registration regression. The complete suite contains 116 tests.

## Version 3.5.0 — 40-minute occupancy budget

Version 3.5.0 raises the default lease, total occupancy budget, CLI command timeout, and Tool schema/runtime ceiling to 2400 seconds. The same clock covers private environment creation, boot, installation, validation, soak time, and artifact export. A 1800-second soak can therefore complete when no other task is waiting. FIFO fairness remains unchanged: once another task waits, the active task must checkpoint at the 120-second slice boundary (or within the bounded late checkpoint window), release without powering off the device, and requeue remaining work. Tests verify the published Tool schema accepts the 2400-second ceiling, an 1800-second command timeout reaches the supervised CLI, and values above 2400 seconds are rejected. The full suite contains 114 tests.

## Local environment

Apple Silicon arm64, macOS 26.6.2, Python 3.14.5, SQLite 3.53.4, Xcode 26.6 (17F113).

## Automated coverage

All 68 tests passed locally after the final watcher handover and creation recovery fixes. A later Linux CI run exposed a 100ms processing deadline for zero-wait requests; zero now reliably means one admission attempt, with a separate watchdog ceiling for a stalled attempt. A slow-housekeeping regression brings the suite to 69 tests. The 61-test suite previously passed locally. A macOS CI SIGTERM run exposed an interrupt arriving immediately after BEGIN; the transaction guard now covers BEGIN itself, and all 21 targeted scheduler tests pass, including a deterministic real-SQLite rollback regression. The complete 62-test suite passed locally after the transaction fix. The suite also includes low-disk startup preflight and stable identity regressions (69 tests total). Real Anthill feedback exposed the old kern.boottime text changing with timezone and NTP microseconds, causing false stale reaping. macOS now uses kern.bootsessionuuid and UTC PID stamps; legacy live clock identities are protected if ambiguous. Upgrade handover uses the incoming stop implementation and restarts older watcher identity protocols. Complete committed creation specs survive delivery failures and can be recovered without adopting devices by name.

- Cross-process FIFO, exclusion, observed intervals, weighted capacities and blocked-head progress.
- Three parallel private creations reserve capacity before SDK calls; a fourth cannot exceed admission.
- Stable per-session/project iOS identities; distinct Android AVDs, writable homes and transactionally reserved ports.
- A single total deadline across creation, boot and validation, short boot budgets, bounded real renewals and no-op renewal allowance.
- Safe waiter yielding, exit 75 and explicitly opted-in FIFO-tail automatic requeue.
- Gradual pressure/recovery hysteresis and immediate creation pause on critical telemetry.
- Active environment protection, exact owned idle iOS shutdown, preserved identity and external/provenance refusal.
- Watcher singleton/restart, deadline cancellation of a live orphan group after supervisor SIGKILL and host reboot running-flag invalidation.
- Bootstrap/config preservation, managed install/upgrade in paths with spaces, shell/JSON output and SDK errors.

SDK automated tests use isolated fake executable tools and temporary state; they do not boot personal devices. Both Skills pass the skill-creator validator. All Python files parse with Python 3.9 grammar, shell entry points pass `sh -n`, and `git diff --check` passes. The English SVG renders to a visually inspected 2200 × 2200 PNG.

## Real macOS SDK exercise

A separate temporary shared state created a **new** iOS device using the installed iOS 26.5 runtime, recorded its exact UDID and manager boot provenance, and attempted supervised startup. Host telemetry then showed approximately 1.9 GiB free disk and high normalized load; the controller correctly progressed to Traditional Mode while keeping the active allocation protected.

The validation supervisor was deliberately interrupted before boot readiness completed. Its tracked boot workload exited, the lease was released, and manager idle maintenance successfully shut down only that newly created device and verified Shutdown state. The isolated fixture was subsequently removed by its exact known test-only UDID. No personal/pre-existing device was adopted, stopped or erased.

This verifies real creation, startup/interruption handling, lease release, pressure fallback and exact-device idle shutdown. It **does not claim successful app UI validation or completed live iOS boot readiness**. The complete boot/work timing path is covered with fake SDK executables. Run the project's device-targeted checks on a host with sufficient resources for app verification.

Android tools were absent from the tested shell PATH, but actual shared activation found the installed SDK tools through fallback discovery and successfully created a dedicated shutdown AVD from an installed system image. Subsequent supervised private Android boot attempts returned timeout/yield and released leases; startup logs reported HVF disabled and mprotect Permission denied even though emulator -accel-check and kern.hv_support reported support. Hardware/OS incapability is not established; actual boot readiness and app verification remain unconfirmed; adapter boot/idle-shutdown behavior is covered with fake executables.

## CI and boundaries

GitHub Actions runs Python 3.9 on Ubuntu and Python 3.14 on macOS. [Current workflow results](https://github.com/HankHuang0516/simulator-manager/actions/workflows/tests.yml).

Coordination is cooperative for one Mac account and one local shared state. Private devices isolate writable device data, not host/SDK/desktop activity. Strict time policy requires supervised run. Unknown live-owner manual work, process-group escape, external automation workers, ambiguous PID reuse and partial creation and ambiguous shutdown fail closed; interrupted idle shutdown is retried only after its stopper dies and provenance is rechecked. Uninterruptible survivors keep their reservation even beyond the use deadline while safe reclamation waits. Static fallback VMs can stay booted; telemetry governs new admission rather than controlling arbitrary external VMs.

Pause new callers, drain leases/queue and stop the verified watcher before installed-code upgrades.

## Tool onboarding and compliance coaching (3.1.0)

All 100 tests passed locally in 55.206 seconds. Three new compliance tests cover task-specific direct `simctl` guidance, matching-lease acceptance, and resolution after the observed process exits without sending any signal. MCP tests verify the seventh Tool action and durable task-owner registration. Plugin and both Skill validators pass.

The native SwiftUI application compiled for macOS 13, installed into `~/Applications`, opened a localized five-step Quick Start, advanced through every lesson, and returned to a live dashboard with its replay button. A launch-context regression found that a native app starts in `/`, where Homebrew Python could stall during site initialization; the dashboard now sets the CLI directory explicitly before every status process. The corrected dashboard refreshed live state without orphan status processes. Website desktop rendering and the new bilingual post-install section were visually inspected. No live simulator was booted for this UI/onboarding change.

## Warm-release coaching (3.1.1)

All 100 tests passed locally in 54.963 seconds. Existing warm-reuse regressions still prove that consecutive supervised chunks reuse one boot, a matching queued owner protects its warm environment after idle expiry, release does not issue a shutdown, and only exact provenance-verified unleased environments can be retired. Compliance tests now verify that iOS and Android guidance explicitly tells a drifting task to release use rights without `simctl shutdown`, emulator close, or `adb emu kill`. Both installed-Skill copies pass validation; Python/shell/JavaScript syntax, `git diff --check`, and the native SwiftUI macOS 13 build pass. No live simulator was booted for this guidance-only change.

Version 3.1.2 fixes native dashboard upgrade handover. An installer-run app replacement now validates the managed bundle marker, sends SIGTERM only to processes whose executable is the exact managed app path, waits for a clean exit, and defers rather than force-killing if the dashboard does not close. It then opens one new instance without `open -n`, preventing stale Quick Start copy and duplicate menu-bar items. Two exact-path/graceful-signal regressions bring the complete suite to 102 passing tests in 56.524 seconds. The watcher, Codex task processes and simulator runtimes are outside this exact path and are never targeted.

## Native language selection (3.2.0)

The native dashboard now opens in Automatic (Device) mode on first launch and resolves any `zh` device locale to Traditional Chinese; all other device locales use English. The globe menu switches the dashboard and Quick Start immediately between Automatic, English and Traditional Chinese, and an explicit choice persists across a graceful app restart. Accessibility inspection verified the Chinese live dashboard, the English live dashboard, both localized language menus and Automatic returning to Chinese on this Traditional Chinese Mac. The final saved choice was restored to Automatic. All 102 tests pass, both Skill copies validate, Python/shell/JavaScript static checks pass, and the SwiftUI application compiles for macOS 13. No simulator or emulator was booted, stopped or reassigned for this interface-only verification; existing managed work was allowed to finish normally.

## Dock presence (3.2.1)

The native dashboard is a regular macOS application while running: its bundle no longer declares an agent-only UI and the Swift runtime uses the regular activation policy. The Dock icon reopens a hidden floating panel through the existing application reopen delegate, while the menu-bar entry remains available. A regression test builds a temporary bundle and verifies both required policies. The change does not alter scheduler, lease, simulator or emulator lifecycle behavior.

Version 3.2.2 closes a duplicate-dashboard handover gap exposed during live Dock verification. Before copying the build bundle into Applications, installation now gracefully stops only a process executing that exact build-bundle path, then applies the existing exact-path handover to the Applications bundle. A regression copies a temporary managed app and verifies the build path is handed over first. This prevents a prior internal build and the Applications copy from sharing one bundle identifier at the same time; no task, watcher, simulator, emulator, ADB process or lease is targeted.

Version 3.2.3 handles the transient LaunchServices lag observed immediately after that two-bundle handover. Opening the installed app now retries at most three times with a 500 ms pause; it still returns a real error if all attempts fail. A regression verifies one failed open followed by success, including the single bounded pause. Live recovery confirmed the same installed Applications bundle opens normally after the handover.

## Dock identity and detailed activity (3.3.0)

The app now embeds a dedicated 1024 px RGBA routing icon and a complete macOS ICNS family. Production review covered the master on light and dark backgrounds, nearest-neighbor inspection at 16, 32 and 64 px, transparent corner pixels, safe alpha bounds, and an ICNS round trip containing every required representation. The first generated concept was rejected because its phone-like details blurred at small sizes and stray pixels escaped the squircle; the final asset uses three abstract simulator nodes, a stronger central routing portal and clean transparent margins.

Recent Activity now persists and displays task/session identity, project, platform, resource, start/end times and total occupancy for completed allocations; active allocations show a live elapsed duration. Migration adds nullable event fields without discarding existing history, and display-time backfill uses retained session/environment metadata for older rows. A scheduler regression verifies the completed release record. The dashboard bundle regression also verifies that the custom ICNS is copied and declared in `Info.plist`.

All 106 tests pass locally. Both Skill copies validate, Python/shell/JavaScript static checks and `git diff --check` pass, and the native SwiftUI bundle compiles for macOS 13 with the icon resource present. The localized website was visually inspected in both Traditional Chinese and English with the new favicon, navigation mark and hero icon. No simulator or emulator was booted, stopped or reassigned for this dashboard and activity-history change.

Version 3.3.1 fixes the fresh/managed installation path exposed by the live upgrade: `install.py` now copies the tracked `assets` directory beside the installed dashboard source. The isolated-install regression verifies both the ICNS and PNG master survive initial install and upgrade, allowing `sim-manager ui --install-app` to build from the managed prefix.

## Mandatory warm release and singleton dashboard (3.4.0)

Every supervised Tool/CLI command is now inspected before admission and explicit `simctl shutdown` or `adb emu kill` actions fail without creating a lease or queue entry. The read-only compliance audit classifies either action as `unsafe-shutdown` and produces specific coaching even when the task owns a valid platform lease; ordinary matching-lease runtime work remains accepted. The Skill, MCP descriptions, Quick Start, session activation guide and bilingual website all state that success, failure, timeout, interruption and cleanup return use rights while leaving the runtime warm. Manager maintenance remains the only component allowed to retire an exact provenance-verified unleased private device.

The native dashboard holds a nonblocking per-user advisory lock for its whole process lifetime. A competing CLI, Dock, Finder, installer or direct-binary launch posts a distributed reveal request, activates the existing application and exits before constructing another panel or menu-bar item. The lock is automatically released if the process exits or crashes. Upgrade replacement still uses the existing exact-managed-executable graceful handover.

All 111 tests pass locally. New regressions cover pre-admission iOS/Android shutdown rejection, shutdown coaching despite a valid lease, Android action classification, MCP rejection, the native singleton primitives and onboarding focus behavior. Both Skill copies validate, Python/shell/JavaScript and whitespace checks pass, and Swift compiles the dashboard for macOS 13.

Version 3.4.1 corrects the Plugin manifest version that remained at 3.3.1 in the initial 3.4.0 package. A regression now requires the core CLI version, MCP server version and `.codex-plugin/plugin.json` version to match, preventing Codex from installing current Tool code under a stale cache identity. The complete suite contains 112 tests. Its full rerun also exposed a pre-existing renewal test that gave dynamic environment creation only one second; the fixture now grants normally and transactionally shortens only its renewable deadline, preserving the policy assertion without coupling it to host delivery speed.

## Native dashboard

The SwiftUI floating panel compiled on Apple Silicon with Swift 6.3.3 targeting macOS 13. Actual accessibility state and screenshot showed live task allocations, countdowns, pressure mode transitions and host metrics. Pin/unpin and session disclosure controls were exercised. The sample-data preview was rendered by the native app, not a mock web page. The macOS CI job also compiles the dashboard without launching it.

## Android root target regression

The Anthill task demonstrated the startup cause: avdmanager had produced target=android-0 for an Android 36.1 image. Correcting only its private manifest to android-36 preserved the 36.1 image and yielded sys.boot_completed=1, ro.build.version.sdk=36 and exit 0 / released true. No SDK security/signature changes were needed. This confirms boot readiness for that task, not game UI validation. New creation validates target/manifest paths, preserves numeric image versions and data, refuses name collisions, and offers explicit leased private target repair with idle/port/image provenance checks. All 76 tests passed in the complete local rerun (67.187 seconds under concurrent host load), and all 76 passed on macOS/Python 3.14 and Ubuntu/Python 3.9 CI. One earlier local run hit a SQLite lock in the pre-existing eight-process exclusion test; the targeted recheck and full rerun both passed. CI: https://github.com/HankHuang0516/simulator-manager/actions/runs/34953674876.

## Warm reuse regression

All 83 tests passed locally in 49.049 seconds on the final 2.0.2 source. Seven new tests cover consecutive supervised chunks with exactly one VM boot, pressure stages within capacity, pending owner protection after idle expiry, unrelated GUI queues, cold-head slot reclamation, foreground waiters blocked by GUI ownership, and reclaiming only excess warm capacity. Existing exact-identifier/provenance shutdown and active lease safety tests still pass. Skill validation, compileall and diff whitespace checks pass. Live host SDK boot was not repeated for this change; the shared ADB remote-stop requester remains unknown and is a separate validation blocker.

## Android transport readiness regression

All 91 tests passed locally in 73.491 seconds. Eight new cases cover stale assigned serial disappearance before cold boot, transient warm AVD-name and boot-property failures without relaunch, an unidentified other transport disappearing before admission, persistent unknown-transport refusal with no child/launch and verified release, changing-inventory identity rechecks, original-deadline command caps, exiting-port readiness and persistent occupied-port refusal. Existing duplicate AVD, foreign serial, external attachment, provenance, fair-use and warm-reuse coverage still passes. The direct fake-VM test fixture was subsequently changed to retain and reap its subprocess handle; its targeted recheck passed (1 test, 1.643 seconds) without a subprocess ResourceWarning. Skill frontmatter validation, compileall and diff whitespace checks pass.

The Anthill task separately reported successful real cold boot (API36, guest MemTotal2017772kB, correct AVD) and an app homepage screenshot before this patch was installed. That recovery is consistent with a transient connection window, not proof that this patch was exercised or that shared ADB remote stops are solved. No diagnostic-only live boot, SDK security change, userdata reset, global policy extension or shared ADB restart was performed for this regression.

Both macOS/Python3.14 and Ubuntu/Python3.9 passed all91 tests on commit8c3e0da; macOS native floating dashboard compilation also passed. CI: https://github.com/HankHuang0516/simulator-manager/actions/runs/34975842796 .
