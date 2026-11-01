/* Region page: draws the region map, makes each route rectangle clickable, and opens
   a route in the bench (index.html) with its spec pre-filled. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const STORE_KEY = "pokeroutes-webui-v1";     // app.js reads its saved params from here
  const REGION_KEY = "pokeroutes-region-spec"; // region-bridge.js adds this to /api/generate
  let data = null, seed = 1;

  async function load() {
    seed = Math.max(0, parseInt($("seed").value, 10) || 0);
    $("status").textContent = "generating (first time takes a few seconds)...";
    const r = await fetch(`/api/region?seed=${seed}`);
    data = await r.json();
    if (data.error) { $("status").textContent = data.error; return; }
    $("map").src = `/api/region.png?seed=${seed}&t=${Date.now()}`;
    const [W, H] = data.size;
    const svg = $("ov");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.innerHTML = "";
    data.routes.forEach((rt) => {
      const [x0, y0, x1, y1] = rt.rect;
      const el = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      el.setAttribute("x", x0); el.setAttribute("y", y0);
      el.setAttribute("width", x1 - x0); el.setAttribute("height", y1 - y0);
      el.setAttribute("class", "r");
      const t = document.createElementNS("http://www.w3.org/2000/svg", "title");
      t.textContent = `${rt.name}: ${rt.role.replace(/_/g, " ")}`;
      el.appendChild(t);
      el.onclick = () => select(rt, el);
      svg.appendChild(el);
    });
    $("status").textContent = `${data.routes.length} routes, ${data.cities.length} towns`;
  }

  function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

  async function select(rt, el) {
    document.querySelectorAll(".r.sel").forEach((n) => n.classList.remove("sel"));
    el.classList.add("sel");
    const spec = await (await fetch(`/api/region-spec?seed=${seed}&id=${encodeURIComponent(rt.id)}`)).json();
    const reg = spec.region || {};
    const exits = (spec.exits || []).map((e) => `<li>${esc(e.side)} at ${e.at} &rarr; ${esc(e.to)}</li>`).join("");
    const mix = Object.entries((reg.biome || {}).mix || {}).map(([k, v]) => `${esc(k)} ${Math.round(v * 100)}%`).join(", ");
    const grad = (reg.biome || {}).gradient || {};
    $("info").innerHTML = `
      <h2>${esc(spec.name)}</h2>
      <div class="muted">${esc(reg.role || "")} (${esc(reg.shape || "")})${(reg.tags || []).length ? " &middot; " + esc(reg.tags.join(", ")) : ""}</div>
      <p>${spec.size[0]} x ${spec.size[1]} metatiles, ${esc(spec.biome)}; ${esc(mix)}</p>
      ${grad.from ? `<p>Gradient: ${esc(grad.from)} &rarr; ${esc(grad.to)} (${esc(grad.axis)})</p>` : ""}
      <p>Exits:</p><ul>${exits}</ul>
      ${(reg.landmarks || []).map((l) => `<p><b>${esc(l.name)}</b>: ${esc(l.story)}</p>`).join("")}
      <p>Rivers: ${(reg.rivers || []).length}, waterfalls: ${(reg.waterfalls || []).length}, coast: ${esc((reg.coast || {}).sides || [])}</p>
      <p class="muted">Elevation tiers (0 low .. 4 peaks):</p>
      <pre class="tiers">${esc(((reg.elevation || {}).tiers || []).join("\n"))}</pre>
      <button class="go" id="open">Open in route bench</button>`;
    $("open").onclick = () => openInBench(spec);
  }

  function openInBench(spec) {
    const p = {
      name: spec.name, seed: spec.seed, width: spec.size[0], height: spec.size[1],
      special_areas: (spec.special_areas || []).slice(0, 6),
      exits: (spec.exits || []).slice(0, 4).map((e) => ({ side: e.side, at: e.at, width: e.width, to: e.to })),
    };
    // the bench needs two exits; a dead end gets a stand-in exit on its goal side
    if (p.exits.length < 2) {
      const g = (spec.region || {}).goal;
      p.exits.push({ side: g ? g.side : "north", at: 0.5, width: 2, to: "" });
    }
    for (const k of ["biome", "meander", "branches", "loops", "dead_ends", "clearings", "lakes", "tall_grass",
      "hidden_paths", "hidden_items", "max_lanes", "dead_end_chance", "ledge_chance"]) {
      if (spec[k] !== undefined) p[k] = spec[k];
    }
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify(p));
      localStorage.setItem(REGION_KEY, JSON.stringify({ name: spec.name, region: spec.region, endings: spec.endings || [] }));
    } catch (e) { /* storage blocked: the bench opens with its defaults */ }
    location.href = `/?region=${encodeURIComponent(seed + "/" + spec.id)}`;
  }

  $("load").onclick = load;
  $("seed").addEventListener("keydown", (e) => { if (e.key === "Enter") load(); });
  load();
})();
