# Shared resource workflow

The [SVG](../assets/flowchart.svg) is editable; the [PNG](../assets/flowchart.png) is optimized for GitHub display.

```mermaid
flowchart TD
    A[Each session: Skill + GitHub link] --> B[Install once / reuse shared state]
    B --> C[Register session and report platform readiness]
    C --> D[Build and host unit tests]
    D --> E{Runtime or UI required?}
    E -->|No| F[Continue without a reservation]
    E -->|Yes| G[Join the shared FIFO queue]
    G --> H{Dedicated slot and budget available?}
    H -->|No| I[Wait / clean dead waiters / respect timeout]
    I --> H
    H -->|Yes| J[Acquire private lease token]
    J --> K[Record PID, start stamp, boot identity and work group]
    K --> L[Open execution gate]
    L --> M[Boot exact UDID / serial and validate]
    M --> N[Finish or cancel own work]
    N --> O[Release own reservation; keep managed VM running]
    O --> P[Next waiting session gets the resource]
    K -. Owner crashes .-> Q{Work group still alive?}
    Q -->|Yes| R[Keep reservation; never steal live work]
    R --> Q
    Q -->|No| S[Reclaim dead lease on next manager check]
    S --> P
    I -->|Timeout| T[Report unavailable validation; do not bypass queue]
```

FIFO is strict within a pool; the oldest satisfiable pool head is chosen across pools. Live-owner TTL expiry prevents new work but retains its reservation. Activation records cooperative intent and never reserves a device by itself.
