# Validation report

Validated on September 15, 2026. Version 2.0.0.

## Local environment

Apple Silicon arm64, macOS 26.6.2, Python 3.14.5, SQLite 3.53.4, Xcode 26.6 (17F113).

## Automated coverage

The 61-test suite passed locally. A macOS CI SIGTERM run exposed an interrupt arriving immediately after BEGIN; the transaction guard now covers BEGIN itself, and all 21 targeted scheduler tests pass, including a deterministic real-SQLite rollback regression. The complete 62-test suite passed locally after the transaction fix. The suite also includes low-disk startup preflight and stable identity regressions (66 tests total). Real Anthill feedback exposed the old kern.boottime text changing with timezone and NTP microseconds, causing false stale reaping. macOS now uses kern.bootsessionuuid and UTC PID stamps; legacy live clock identities are protected if ambiguous.

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

Android tools were absent from the tested shell PATH, but actual shared activation found the installed SDK tools through fallback discovery and successfully created a dedicated shutdown AVD from an installed system image. Real Android VM startup/app verification remains untested; adapter boot/idle-shutdown behavior is covered with fake executables.

## CI and boundaries

GitHub Actions runs Python 3.9 on Ubuntu and Python 3.14 on macOS. [Current workflow results](https://github.com/HankHuang0516/simulator-manager/actions/workflows/tests.yml).

Coordination is cooperative for one Mac account and one local shared state. Private devices isolate writable device data, not host/SDK/desktop activity. Strict time policy requires supervised run. Unknown live-owner manual work, process-group escape, external automation workers, ambiguous PID reuse and partial creation and ambiguous shutdown fail closed; interrupted idle shutdown is retried only after its stopper dies and provenance is rechecked. Uninterruptible survivors keep their reservation even beyond the use deadline while safe reclamation waits. Static fallback VMs can stay booted; telemetry governs new admission rather than controlling arbitrary external VMs.

Pause new callers, drain leases/queue and stop the verified watcher before installed-code upgrades.
