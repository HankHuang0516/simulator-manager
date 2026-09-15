# Shared simulator / GUI resources

Source: https://github.com/HankHuang0516/simulator-manager
Enable this session using its SESSION_START.md procedure.

Before iOS/Android runtime or GUI testing, read and follow the installed
`simulator-manager` Skill. Run applicable builds and host unit tests first;
application XCTest and Android instrumented tests need a lease.
Use the shared `sim-manager run ios|android --boot ... -- COMMAND` lifecycle.
All projects use the same `SIM_MANAGER_STATE_DIR` and pool configuration.
Target only the assigned `SIM_MANAGER_UDID` or `SIM_MANAGER_SERIAL`.
Never use implicit targets, arbitrary boot/shutdown, `shutdown all`, `erase all`,
or `adb kill-server`. Release your own token on completion or failure.
For foreground GUI automation, configure the shared `global_capacity` to 1.
Do not nest leases or bypass a busy queue.
