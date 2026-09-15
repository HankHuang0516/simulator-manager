---
name: simulator-manager
description: Enable cooperative sharing of macOS simulators and GUI test resources across Codex sessions using a private Dynamic Simulator Pool, pressure-aware Traditional FIFO fallback, bounded occupancy and safe requeue.
---

# Simulator manager — GitHub entry point

Read [the complete Skill](skill/simulator-manager/SKILL.md), then follow
[SESSION_START.md](SESSION_START.md) to enable this session.

The repository contains the Skill, executable manager, idempotent bootstrap,
flowchart, configuration examples, and tests. Install the complete project,
not only this entry-point file. The installed Skill is `skill/simulator-manager`.

Repository: https://github.com/HankHuang0516/simulator-manager

To view scheduling after activation, run `sim-manager ui` using the installed CLI. It builds and opens the native macOS floating dashboard. See [README.md](README.md#floating-dashboard).
