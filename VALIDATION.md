# Validation report

Validated on September 15, 2026.

## Local environment

Apple Silicon arm64, macOS 26.6.2, Python 3.14.5, SQLite 3.53.4, Xcode 26.6 (17F113).

## Results

- **41 tests passed** with `python3 -m unittest discover -s tests -v`.
- Two simultaneous bootstrap processes installed once, registered both sessions, and reused the same shared state.
- Repeated bootstrap preserved existing pool configuration byte-for-byte.
- Dedicated provisioning tests verified iOS creation without boot, private Android AVD home, installed system-image selection, paused-pool preservation, deferred setup during active work, and useful missing-SDK readiness results.
- Multi-process FIFO and observed workload intervals verified resource exclusion, pool capacity, weighted shared budget, and progress around a blocked head in another pool.
- Crash/interruption tests verified dead-owner reclamation, surviving child/grandchild protection after SIGKILL, cancellation after SIGTERM, live-owner TTL protection, and gate EOF before workload execution.
- The previous SIGTERM stress pass completed 20/20 repetitions; Darwin's empty/zombie-only process-group EPERM case was addressed.
- Fake executable SDK adapters verified pinned UDID/AVD/serial/port, runtime reuse, external-device refusal, duplicate-AVD refusal, and lease release on boot failure/timeout.
- Installation was exercised in temporary paths containing spaces, including executable symlink use, managed upgrades, config preservation, and overwrite refusal.
- Root and installed Skill frontmatter passed the `skill-creator` validator.
- All Python sources parsed with Python 3.9 grammar; both shell entry points passed `sh -n`.
- The English SVG was rendered to a 2200 × 2200 PNG and visually inspected for text, branch arrows, layout, and clipping.

## Real SDK scope

Actual `xcodebuild -version`, `xcrun simctl help boot`, `bootstatus`, and read-only `simctl list devices --json` succeeded. The previous inventory contained 23 available devices and one booted device. No existing personal device was added to the manager, booted, shut down, or erased.

Android tools were absent from the tested shell PATH. Android behavior and provisioning were validated with isolated fake tools/mocks. No claim is made that a real Android emulator or application UI was tested.

The new automatic provisioning path was tested without creating real devices. A session using `enable --prepare` will attempt dedicated setup using its installed SDKs and report actual platform readiness. Run the project's own device-targeted tests to complete live end-to-end verification.

## Automated checks

The repository includes GitHub Actions jobs for Python 3.9 on Ubuntu and Python 3.14 on macOS. [View current workflow results](https://github.com/HankHuang0516/simulator-manager/actions/workflows/tests.yml). Local validation does not imply that future workflow runs pass.

## Boundaries

This is cooperative coordination for one Mac account using a local state directory. Skill/session registration does not intercept arbitrary SDK commands or enforce policy in sessions that have not adopted it.

Live-owner TTL expiry retains the reservation. Only a proven-dead owner with no live registered workload is reclaimed. Work escaping the registered process group, external GUI workers, rare ambiguous PID/group reuse, and partial dedicated-device creation during a host crash remain documented limitations. Release frees reservations, not VMs. Capacity limits active reservations, not booted VM memory usage.

Pause new callers and drain leases/queues before upgrading installed code.
