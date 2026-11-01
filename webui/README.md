# webui

A rough local bench for watching poke-routes build a route, stage by stage, drawn with Emerald's General + Fortree metatiles.

Run: `python3 webui/server.py`, then open http://localhost:8790 (stdlib server; set `PORT=` to change the port).

## Improve box (rough, unattended)

An attempt at Patrick Collison's "liquid software" idea: the small **Improve** button (bottom right, on every page that includes `static/improve.js`) opens a box where you type how the software should be different. The server queues it and runs Claude Code headless on it, one job at a time, and the run log streams into the panel.

- Targets: **Generator & web UI** (this repo) or **ROM** (`arklarna/rom`). ROM jobs work in a fresh worktree `../rom-wt/improve-<id>` on branch `arklarna/improve-<id>`, must build (`make -j10`) and be screenshotted with `tools/romshot`, and only then get squash-merged into `arklarna` and copied to `release/v1/arklarna-v1.gba`.
- Every job should end in exactly one commit starting with `improve: `. **Undo** reverts it (one revert commit; for the ROM it also rebuilds and recopies the release ROM). Undo refuses if there are staged changes or uncommitted edits to the files that commit touched; other agents' unrelated work in the tree is left alone (jobs are told to stage only their own files).
- Run: `claude -p <prompt> --output-format stream-json --verbose --permission-mode bypassPermissions --strict-mcp-config --no-chrome --disallowedTools "Bash(git push:*)" ...`, cwd = the target repo, 20 min timeout (the process group is killed). No MCP servers are loaded. The prompt (see `improve.py`) adds the repo's purpose, how to verify, the owner's rules from `documenting-agent-work/decisions.md` and `AGENT_BRIEF.md`, and hard limits: stay inside the repo, no pushing, no remotes, no network-facing changes, no secrets.
- The server only listens on 127.0.0.1, and `/api/improve*` also checks `Host`/`Origin` and requires a JSON body, so another web page can't submit jobs through your browser.
- History: `webui/improvements.json`; logs and prompts: `webui/improve-logs/` (both gitignored). Each job also adds a line to `arklarna/documenting-agent-work/log/<date>.md`.
- If a job changes `webui/*.py`, restart the server to load it (static files and the generator reload on their own).

API: `POST /api/improve {request, target}`, `GET /api/improve` (history), `GET /api/improve/<id>?since=N` (status + log lines), `POST /api/improve/<id>/undo`, `POST /api/improve/<id>/cancel`.
