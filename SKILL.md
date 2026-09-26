---
name: simulator-manager
description: Enable cooperative sharing of macOS simulators and GUI test resources across Codex sessions using a warm shared FIFO pool, atomic multiplayer device groups, bounded occupancy and safe requeue.
---

# Simulator manager — GitHub entry point

Read [the complete Skill](skill/simulator-manager/SKILL.md), then follow
[SESSION_START.md](SESSION_START.md) to enable this session.

The repository contains the Skill, executable manager, idempotent bootstrap,
flowchart, configuration examples, and tests. Install the complete project,
not only this entry-point file. The installed Skill is `skill/simulator-manager`.

Repository: https://github.com/HankHuang0516/simulator-manager

To view scheduling after activation, run `sim-manager ui` using the installed CLI. It builds and opens the native macOS floating dashboard. See [README.md](README.md#floating-dashboard).

Every session must release after runtime testing without powering off its assigned simulator or emulator. Explicit `simctl shutdown` and `adb emu kill` actions are rejected; only manager maintenance may retire a verified unleased runtime. The dashboard is a per-user singleton, so every launch focuses the one existing main UI.
