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

## Region editor (rough)

`/editor.html` (linked from the bench and `/region.html`): the world map that `pokeroutes.region` generates, drawn on a canvas so it can be fixed by hand. Open with `?seed=N` or `?load=NAME`.

- Layers: hillshaded height, biomes, water (sea, lakes, rivers), routes (role colours; dashed border = unpinned), labels. Wheel or `+ - 0` to zoom, space / right / middle drag to pan.
- Tools (keys in brackets, `?` shows them all): select `V` (drag towns and landmarks, drag a selected route's edges or exits, double-click a route to open it in the bench), raise `R` / lower `L` / flatten `F` / smooth `S`, paint biome `B`, coast `W` (land / sea / lake), river `I` (draw / erase, Alt erases), add route `A` (drag from a town or route to a town, route or landmark). `[ ]` brush size.
- Every edit marks the downstream stages stale. Recompute runs them on the server (`pokeroutes/region/edit.py`): water (lakes, rivers, flow), climate, biomes, routes. Hand-painted biome / coast / river cells are kept as overrides; pinned routes stay put and only the missing pieces of their links are re-laid. Editing a route (spec field, rect, exit, name, role) pins it.
- Route inspector: name, pin, role, biome, size, meander / branches / ... / special_area_chance / weather (hand-set values are marked `*` and can be reset), exits with a slider each, warnings (over the GBA budget, exits not flush, overlaps).
- Deleting a route drops its link from the plan and pins what's left of it, so recompute doesn't fill the gap back in.
- Undo / redo: 100 steps, copy-on-write grids. Save writes `region_out/<name>/region.json` (the usual summary plus an `editor` block with the whole document), `routes/*.yaml` and `region_map.png`; Load reads it back. Export specs downloads the same as a zip.
- Embeddable: `PREditor.init(container, opts)` in `static/editor/app.js` builds the editor inside any element and returns its actions (undo, redo, save, load, generate, exportSpecs, recompute, setTool, toggleLayer, fit, openInBench, destroy).
- Known gaps: moving a legendary landmark moves its marker (and what routes aim for), not the terrain it carved. Town-to-town links often find no free corridor because route rects may not overlap. Starting from a nearby route works better. A water recompute with no edits still shifts a few river cells, because rain follows the latest climate.

API: `POST /api/editor/{generate,load,recompute,add-route,save,spec,render,export}`, `GET /api/editor/list` (see `editor_api.py`).

## Bench paint

**Paint** in the bench toolbar (or `P`) switches to the finished stage and lets you paint semantic classes (tree, grass, path, tall grass, water, rock, ledges, waterfall) cell by cell. `POST /api/retile` re-tiles them with the same tiler, keeping every metatile whose 3x3 class neighbourhood didn't change, so the Porymap export zip carries the painted map. Paint is remembered per route name and size and put back after every regenerate (other seeds too) until you press clear.

Tests: `node webui/tests/editor-logic.test.js` (pure editor logic: grids, brushes, undo/redo, route edits).
