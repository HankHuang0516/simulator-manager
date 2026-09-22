# Dynamic Simulator Pool workflow

[Full-resolution PNG](../assets/flowchart.png) · [Editable SVG](../assets/flowchart.svg)

```mermaid
flowchart TD
    Enable[Enable simulator-manager Skill in each session] --> Build[Build and host unit tests first]
    Build --> Need{Runtime or UI required?}
    Need -- No --> Continue[Continue without simulator occupancy]
    Need -- Yes --> Queue[FIFO admission: wait without occupying a device]
    Queue --> Grant[Lease grant: start one total occupancy clock]
    Grant --> Mode{Pressure-aware admission}
    Mode -- Default --> Dynamic[Dynamic Simulator Pool: private device per session + project]
    Mode -- Reduced admission --> Traditional[Traditional Mode: serial private reuse or configured shared slot]
    Dynamic --> Create[Create if missing, then boot exact assigned device]
    Traditional --> Create
    Create --> Work[Runtime verification in registered workload group]
    Work --> Waiting{Someone waiting?}
    Waiting -- Yes --> Checkpoint[Finish or checkpoint at waiter slice boundary]
    Waiting -- No --> Finish[Finish within total and phase deadlines]
    Checkpoint --> Stop[Safely stop own work; confirm complete group exit]
    Finish --> Stop
    Stop --> Release[Release token on success, failure or interruption]
    Release --> Next[Next eligible waiter proceeds]
    Next -- Remaining validation --> Queue
    Release --> Idle[Idle private VM may be stopped by manager; keep data]

    Watch[Watcher: memory pressure, normalized load, free disk] --> Stage[Dynamic → Constrained → Draining → Traditional]
    Stage --> Mode
    Watch --> Recover[10 healthy samples per upward stage]
    Recover --> Mode
    Crash[Supervisor crash] --> Protect[Keep live tracked group reserved]
    Protect --> Deadline[Watcher cancels only orphan group at original deadline]
    Deadline --> Stop
```

Defaults: total use budget 2400 seconds (40 minutes) including creation + boot + installation + validation + export; at most 3 actual extensions; waiter slice 120 seconds and late checkpoint window 10 seconds. A 1800-second soak fits only while no waiter arrives. Expired leases cannot be revived. Exit 75 requires remaining work to requeue at the FIFO tail; automatic rerun is opt-in for explicitly restartable commands only.

Pressure uses 2-second sampling and 3 elevated samples per downward step; critical telemetry immediately pauses new private creation. New private creation pauses first, then admissions reduce and Traditional scheduling applies. Active environments are never evicted. Only provenance-verified unleased private VMs can be stopped by exact identifier, at most one per sampling interval. Private assignment/data persists; another session never receives it.

Supervised run enforces policy. Unknown live manual work stays protected; cancellation cannot promise arbitrary side-effect rollback. Device-data isolation shares the Mac host, SDKs and desktop. Foreground mobile requests use `--foreground`, avoiding nested GUI reservations.
