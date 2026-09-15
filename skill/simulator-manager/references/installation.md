# Installation

Repository: https://github.com/HankHuang0516/simulator-manager

Run `./bootstrap.sh --session ACTUAL_TASK_ID` after cloning the repository.
It installs the manager once, enables this session, and prepares dedicated
shutdown devices where installed SDK components are available.

Default CLI: `~/.local/bin/sim-manager`.
Default shared state: `~/Library/Application Support/simulator-manager`.
Bootstrap uses `~/.agents/skills`, or reuses a managed legacy Skill under
`${CODEX_HOME:-~/.codex}/skills`. Do not install duplicate same-name Skills.
The installer replaces this reference with exact installed paths.

Use the same shared state/config for every project and session. The Skill
alone does not provide queue coordination; the installed CLI does.
