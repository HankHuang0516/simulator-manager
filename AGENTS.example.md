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
Default to Dynamic Simulator Pool; accept gradual pressure-aware Traditional fallback.
Keep the returned session label and project path stable for private device reuse.
Total occupancy includes creation + boot + work (default 600 seconds);
at most 3 real renewals, never beyond the original hard deadline.
When another session waits, checkpoint/finish within the waiter slice, release,
and request remaining work again at the FIFO tail. Exit 75 means requeue required.
Only explicitly restartable scripts may opt into automatic requeue.
For foreground GUI automation, add `--foreground` to the mobile request.
Only manager maintenance may stop an unleased, proven-owned private VM; keep data.
Do not nest leases or bypass a busy queue.
