# Enable this session

This is the agent procedure for the user request:

> Use the simulator-manager Skill from https://github.com/HankHuang0516/simulator-manager and enable shared mode for this session.

1. Read `skill/simulator-manager/SKILL.md` from this repository. Adopt its rules for the current session, including builds/host tests first, explicit device targets, supervised runtime work, and release on completion/failure.
2. If a managed `sim-manager` version 1.1.0 is already installed, use its absolute path and run `enable --session ACTUAL_SESSION_LABEL --project ACTUAL_PROJECT_PATH --prepare --json`. Read its installed `references/installation.md` to preserve the shared state path. Use the same custom `--state-dir` when one was previously configured. Do not create a per-project state directory.
3. Otherwise clone this repository into a newly created temporary directory, then execute `bootstrap.sh --session ACTUAL_SESSION_LABEL --project ACTUAL_PROJECT_PATH`. Do not overwrite an existing checkout. Bootstrap installs once, serializes concurrent installation, reuses managed files, and preserves the pool config. It defaults to the documented user Skill directory `~/.agents/skills`, or reuses a managed legacy installation.
4. Save the returned session label, CLI path, and state path in the current session context. If there is no actual task ID available, let bootstrap generate the label and use that returned label consistently. A registration label is attribution, not a process lease. Do not invent an owner PID.
5. Read `platforms` in the response. Automatic setup creates new dedicated **shutdown** devices only from installed SDK components. It does not boot devices, download runtimes, adopt personal simulators, or overwrite a disabled/custom pool. If SDK components are missing or setup is deferred, shared mode is enabled but that platform is not ready; report the exact reason and retry `setup ios|android` when runtime validation is needed.
6. Confirm shared mode is enabled with the session label and platform readiness. From this point, use `sim-manager run ios|android --session SAVED_LABEL --boot ... -- COMMAND` for runtime/UI work. Explicitly pass a custom shared state path on every call. Do not acquire for builds or host tests that do not need runtime.

Example bootstrap after cloning (substitute real session/project values):

```sh
./bootstrap.sh --session 'actual-task-id' --project '/absolute/project/path'
```

Existing installed CLI:

```sh
~/.local/bin/sim-manager enable --session 'actual-task-id' --prepare --json
```

If an older managed version is installed, finish existing work and pause new callers before `bootstrap.sh --upgrade`. Do not replace installed code while other sessions are using it.

The user instruction authorizes this session to participate. The Skill and session registration do not enforce behavior in other sessions: each session must receive the same instruction or follow shared project rules. Keep the current session in this mode until the user changes that preference.
