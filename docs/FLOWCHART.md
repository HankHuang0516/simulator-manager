# Warm shared pool workflow

```mermaid
flowchart TD
    Enable[Enable simulator-manager for each task] --> Build[Build and run host unit tests]
    Build --> Need{Runtime or UI verification needed?}
    Need -- No --> Host[Continue without device occupancy]
    Need -- Yes --> Size{How many same-platform devices?}
    Size -- One --> Request[Request one managed lease]
    Size -- Multiplayer N --> Group[Request --count N as one atomic group]
    Request --> FIFO[FIFO queue]
    Group --> FIFO
    FIFO --> Available{Entire request available within capacity?}
    Available -- No --> Wait[Wait without holding a partial group]
    Wait --> Available
    Available -- Yes --> Grant[Grant all leases and start original total deadline]
    Grant --> Boot[Boot exact assigned UDIDs or serials]
    Boot --> Test[Run one supervised workload group; target every device explicitly]
    Test --> Yield{Another task waiting or deadline reached?}
    Yield -- Yes --> Checkpoint[Finish or checkpoint safely]
    Yield -- No --> Finish[Finish validation and export]
    Checkpoint --> Release[Release every lease; leave devices warm]
    Finish --> Release
    Release --> Next[Next FIFO request receives reused devices and their retained data]
    Next -- Remaining work --> FIFO

    Pressure[Watcher samples host pressure] --> Limits[Bound new admissions; never evict active work]
    Limits --> Available
    Crash[Owner or supervisor crash] --> Recovery[Protect live tracked work; reclaim proven-stale leases]
    Recovery --> Available
```

The default is a bounded shared FIFO pool. A same-platform multiplayer request obtains its whole group at once, or waits without occupying a partial set. One maximum 2400-second occupancy deadline includes boot, installation, validation and export; at most three actual renewals stay inside that deadline. A waiting task triggers a 120-second fair-use slice and a bounded 10-second late checkpoint window. Exit 75 requires remaining work to requeue at the FIFO tail. The manager, not a task, may retire an unleased device for a real capacity or critical-pressure need. Releasing use rights does not shut down the device or erase its apps/data.

Optional Dynamic private environments remain available for installations that deliberately enable that mode. They are never lent to another task. Migration to shared mode and private cleanup require zero leases and FIFO waiters; ambiguous or live private devices stay protected.
