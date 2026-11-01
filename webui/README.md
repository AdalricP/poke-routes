# webui

A rough local bench for watching poke-routes build a route, stage by stage, drawn with Emerald's General + Fortree metatiles.

Run: `python3 webui/server.py`, then open http://localhost:8790 (stdlib server; set `PORT=` to change the port).

## Editor shell (rough)

The page is laid out like a small game-engine editor (Unity's dark theme was the reference): a menu bar, a toolbar, docked panels you can resize or collapse, and a status bar. It replaced the old one-page bench after the owner found that hard to use. `/index.html` and `/region.html` now redirect to `/`.

- **Menu bar**: File (new route, open/save spec as JSON, open YAML specs too, recent routes, import, export: Porymap zip, scene.json, PNG of the current stage, region JSON, all region specs), Edit (undo/redo of spec edits and paint strokes, copy/paste spec, preferences), View (panels, zoom, frame selection, overlays, which stage to show), Generate (generate, new seed, region, tiler: learned or Ark rule tiles, stage stepping), Window (scene / region / world views, console, timeline, Improve, reset layout), Help (shortcuts, about). Menus show Cmd on macOS and Ctrl elsewhere.
- **Shortcuts** are all in `SHORTCUTS` in `static/shell-logic.js`. Single keys (Space, arrows, V/H/B/S tools, F frame) only work when no text field or slider has focus. Generate is Cmd/Ctrl+G and never fires from a text field; the old Ctrl+Enter is gone.
- **Layout**: Hierarchy on the left (exits, lanes, special areas, clearings and lakes, items and events, layers with visibility toggles; click selects, double-click frames). The centre has Scene / Region / World tabs. Inspector on the right, full height: the selected object, then the spec fields in foldouts, the route report and the encounter tables. The bottom dock has Console (log, warnings, generation report), Timeline (stage ruler with change keys, plus zones and items added per stage) and Improve. Hierarchy, Inspector and the bottom dock can also be torn off Unity-style: drag a panel's tab strip to make it a floating window you can move around and resize from its corner; drop it back on its own screen edge (a blue box shows where it lands), double-click its tab strip or press its corner button to dock it again. They only dock back to their own edge for now, not to each other's. Panel sizes, open/closed and floating docks, tabs and foldouts are kept in localStorage (`pokeroutes-shell-layout-v1`); Window > Reset layout clears them.
- **Tools**: select/pan, paint (classes) and stamp (tree block, pond, grass patch...); see "Bench paint" below. Ark-tiled routes can't be painted yet.
- **World tab**: hosts the region editor (`static/editor.html`) in a frame, so its stylesheet stays separate. File > Save, Edit > Undo/Redo, Generate > Generate region and File > Export > All region specs act on the editor while that tab is open (through its `window.editor` actions). Its "open in bench" lands in the Scene tab instead of a new browser tab.
- **Region tab**: the old region page. Click a route box to see it in the Inspector; "Open in Scene" (or double-click) loads its spec plus its region block and generates it. "Edit in World" opens the same seed in the World tab.
- Files: `static/shell.html`, `shell.css` (theme), `shell.js` (menus, docks, console, keys), `bench.js` (scene, inspector, hierarchy, timeline, region/world views, files), `shell-logic.js` (pure: shortcuts, layout maths, undo, recent list, spec conversion), `webui/shell_api.py` (entry page, redirects, Ark atlas, scene/spec exports, spec parsing).
- Tests: `node webui/tests/shell-logic.test.js`.

Extra API: `GET /api/tilers`, `GET /api/atlas?tiler=ark` (+ `.png`), `POST /api/generate {..., tiler: "ark"}`, `GET /api/scene?id=`, `GET /api/region-specs.zip?seed=`, `POST /api/spec-parse {text}`.

## Improve box (rough, unattended)

An attempt at Patrick Collison's "liquid software" idea: the **Improve** tab in the editor's bottom dock (or a small floating button on any other page that includes `static/improve.js`) is where you type how the software should be different. The server queues it and runs Claude Code headless on it, one job at a time, and the run log streams into the panel.

- **Screenshot** freezes the page, then you drag a box anywhere on it and that box gets attached. **Mark up** freezes it too, you draw on it in red, then attach either the area around the drawing or the whole page. Both go through the browser's tab-share prompt.
- Targets: **Generator & web UI** (this repo) or **ROM** (`arklarna/rom`). ROM jobs work in a fresh worktree `../rom-wt/improve-<id>` on branch `arklarna/improve-<id>`, must build (`make -j10`) and be screenshotted with `tools/romshot`, and only then get squash-merged into `arklarna` and copied to `release/v1/arklarna-v1.gba`.
- Every job should end in exactly one commit starting with `improve: `. **Undo** reverts it (one revert commit; for the ROM it also rebuilds and recopies the release ROM). Undo refuses if there are staged changes or uncommitted edits to the files that commit touched; other agents' unrelated work in the tree is left alone (jobs are told to stage only their own files).
- Run: `claude -p <prompt> --output-format stream-json --verbose --permission-mode bypassPermissions --strict-mcp-config --no-chrome --disallowedTools "Bash(git push:*)" ...`, cwd = the target repo, 20 min timeout (the process group is killed). No MCP servers are loaded. The prompt (see `improve.py`) adds the repo's purpose, how to verify, the owner's rules from `documenting-agent-work/decisions.md` and `AGENT_BRIEF.md`, and hard limits: stay inside the repo, no pushing, no remotes, no network-facing changes, no secrets.
- The server only listens on 127.0.0.1, and `/api/improve*` also checks `Host`/`Origin` and requires a JSON body, so another web page can't submit jobs through your browser.
- History: `webui/improvements.json`; logs and prompts: `webui/improve-logs/` (both gitignored). Each job also adds a line to `arklarna/documenting-agent-work/log/<date>.md`.
- If a job changes `webui/*.py`, restart the server to load it (static files and the generator reload on their own).

API: `POST /api/improve {request, target}`, `GET /api/improve` (history), `GET /api/improve/<id>?since=N` (status + log lines), `POST /api/improve/<id>/undo`, `POST /api/improve/<id>/cancel`.

## Region editor (rough)

The World tab of the editor shell (or `/editor.html` on its own): the world map that `pokeroutes.region` generates, drawn on a canvas so it can be fixed by hand. Open with `?seed=N` or `?load=NAME`.

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

The **Paint** tool in the shell toolbar (`B`; **Stamp** is `S`) switches to the finished stage and lets you paint semantic classes (tree, grass, path, tall grass, water, rock, ledges, waterfall) with a 1-5 cell brush; quick strokes are filled in along the line. Tree and water strokes are at least 2 wide (GBA trees are 2x2 and a 1-cell pond has no tiles), and each stroke gets the generator's cleanup around it (1-wide tree walls left behind become grass, lone water cells become grass), because those shapes were what made painted tiles come out broken. `POST /api/retile` re-tiles them with the same tiler, keeping every metatile whose 3x3 class neighbourhood didn't change, so the Porymap export zip carries the painted map. Paint is remembered per route name and size (localStorage `pokeroutes-paint-v1`) and put back after every regenerate (other seeds too) until you press Clear paint (toolbar, or Edit > Clear painted cells). Strokes are undoable with Cmd/Ctrl+Z.

Tests: `node webui/tests/editor-logic.test.js` (pure editor logic: grids, brushes, undo/redo, route edits) and `node webui/tests/shell-logic.test.js` (shell: shortcuts, layout, undo, paint memory).
