import { FULLSCREEN_VS, GROUND_FS, POST_FS, SPRITE_FS, SPRITE_VS } from "./shaders.js";
import { World } from "./world.js";
import { Music } from "./music.js";
import { Battle, makeMon } from "./battle.js";

const canvas = document.getElementById("gl");
const gl = canvas.getContext("webgl2", { antialias: false, premultipliedAlpha: false });
if (!gl) document.getElementById("loading").textContent = "WebGL2 is needed";

const params = new URLSearchParams(location.search);
if (params.has("clean")) document.body.classList.add("clean");
const SCENES = (params.get("scenes") || "Bernsteinweg,Erlenbruch,Kiefernheide").split(",");
const TILE = 32;
const STEP_TIME = 0.2;     // seconds per tile walking
const RUN_TIME = 0.11;

// ------------------------------------------------------------------ gl helpers
function shader(type, src) {
  const s = gl.createShader(type);
  gl.shaderSource(s, src);
  gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s) + "\n" + src.split("\n").map((l, i) => `${i + 1}: ${l}`).join("\n"));
  return s;
}
function program(vs, fs) {
  const p = gl.createProgram();
  gl.attachShader(p, shader(gl.VERTEX_SHADER, vs));
  gl.attachShader(p, shader(gl.FRAGMENT_SHADER, fs));
  gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
  const u = {};
  const n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS);
  for (let i = 0; i < n; i++) {
    const info = gl.getActiveUniform(p, i);
    u[info.name.replace("[0]", "")] = gl.getUniformLocation(p, info.name);
  }
  return { p, u };
}
function texture(img, { nearest = true, data = null, w = 0, h = 0 } = {}) {
  const t = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, t);
  if (w) gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, data);
  else gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
  const f = nearest ? gl.NEAREST : gl.LINEAR;
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, f);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, f);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  return t;
}
const loadImage = src => new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = rej; i.src = src; });

// ------------------------------------------------------------------ state
let atlas, atlasTex, world, cellTex;
let ground, sprites, post;
let quadVAO, spriteVAO, instBuf;
let fbo, fboTex, viewW = 0, viewH = 0, scale = 3;
const player = { x: 0, y: 0, px: 0, py: 0, dir: "down", moving: false, t: 0, from: null, to: null, hop: 0, frame: 0, steps: 0 };
const cam = { x: 0, y: 0 };
const keys = new Set();
let tod = 1;                 // 0 dawn, 1 day, 2 dusk, 3 night
let sceneIndex = 0;
let mode = "walk";           // walk | dialog | encounter
let dialogQueue = [];
let currentArea = null;
let particles = [];
const music = new Music();
const party = [makeMon("Growlithe", ["Fire"], 18, "rare")];
const bag = { potion: 5, ball: 8 };
let rivalBeaten = false;
const battle = new Battle(music, (result) => {
  mode = "walk";
  music.endBattle();
  if (result === "lost") {
    party.forEach(m => { m.hp = m.maxHp; });
    loadScene(sceneIndex);
  }
  if (result === "won" && battle.kind === "trainer") rivalBeaten = true;
  drawParty();
});

function drawParty() {
  document.getElementById("party").innerHTML = party.map(m =>
    `<div title="${m.name} Lv${m.level}"><img src="assets/mon/${m.slug}/front${m.shiny ? "_shiny" : ""}.png"><i><b style="width:${100 * m.hp / m.maxHp}%"></b></i></div>`).join("");
}

// ------------------------------------------------------------------ setup
async function init() {
  atlas = await (await fetch("assets/atlas.json")).json();
  atlasTex = texture(await loadImage("assets/atlas.png"));
  ground = program(FULLSCREEN_VS, GROUND_FS);
  sprites = program(SPRITE_VS, SPRITE_FS);
  post = program(FULLSCREEN_VS, POST_FS);

  quadVAO = gl.createVertexArray();
  gl.bindVertexArray(quadVAO);
  const qb = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, qb);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
  for (const prog of [ground, post]) {
    const loc = gl.getAttribLocation(prog.p, "aPos");
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
  }

  spriteVAO = gl.createVertexArray();
  gl.bindVertexArray(spriteVAO);
  const cb = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, cb);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([0, 0, 1, 0, 0, 1, 1, 1]), gl.STATIC_DRAW);
  const aCorner = gl.getAttribLocation(sprites.p, "aCorner");
  gl.enableVertexAttribArray(aCorner);
  gl.vertexAttribPointer(aCorner, 2, gl.FLOAT, false, 0, 0);
  instBuf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, instBuf);
  const stride = 15 * 4;
  [["aDst", 4, 0], ["aSrc", 4, 16], ["aParams", 4, 32], ["aTint", 3, 48]].forEach(([name, size, off]) => {
    const loc = gl.getAttribLocation(sprites.p, name);
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, size, gl.FLOAT, false, stride, off);
    gl.vertexAttribDivisor(loc, 1);
  });

  await loadScene(sceneIndex);
  resize();
  addEventListener("resize", resize);
  document.getElementById("loading").style.opacity = 0;
  setTimeout(() => document.getElementById("loading").remove(), 900);
  requestAnimationFrame(frame);
}

async function loadScene(i, entrySide = null) {
  const name = SCENES[i % SCENES.length];
  const scene = await (await fetch(`scenes/${name}.json`)).json();
  world = new World(scene, atlas);
  if (cellTex) gl.deleteTexture(cellTex);
  cellTex = texture(null, { data: world.cellData, w: world.w, h: world.h });
  // start on the first exit (or the one matching where we came from), a few steps in
  let ex = scene.exits[0];
  if (entrySide) ex = scene.exits.find(e => e.side === entrySide) || ex;
  const [ey, exx] = ex.cells[Math.floor(ex.cells.length / 2)];
  const inward = { north: [0, 1], south: [0, -1], west: [1, 0], east: [-1, 0] }[ex.side];
  let sx = exx + inward[0] * 2, sy = ey + inward[1] * 2;
  player.x = sx; player.y = sy; player.px = sx * TILE; player.py = sy * TILE;
  player.dir = { north: "down", south: "up", west: "right", east: "left" }[ex.side];
  player.moving = false;
  cam.x = player.px - viewW / 2; cam.y = player.py - viewH / 2;
  currentArea = null;
  particles = [];
  showPlaque(scene.name, routeSubtitle(scene));
  placeRival();
  drawParty();
  drawMinimap();
  music.setScene(scene);
}

function placeRival() {
  if (sceneIndex % SCENES.length !== 0) return;
  // on the main road, about a third of the way in, facing the player's approach
  const main = world.scene.zones.find(z => z.kind === "main").cells;
  for (let i = Math.floor(main.length * 0.3); i < main.length; i++) {
    const [y, x] = main[i];
    if (world.isBlocked(x, y) || world.cls(x, y) === "t") continue;
    const src = atlas.sprites[atlas.groups.rival_down[0]];
    const sprite = world.addSprite(src, x * TILE - 16, y * TILE - 30, { sortY: y * TILE + 30 });
    world.objects.set(`${y},${x}`, { kind: "rival", sprite });
    world.blocked[y * world.w + x] = 1;
    world.rivalAt = [x, y];
    break;
  }
}

function routeSubtitle(scene) {
  const n = scene.zones.filter(z => z.kind === "special").length;
  return `${scene.width}×${scene.height} · ${n} hidden place${n === 1 ? "" : "s"} · seed ${scene.seed}`;
}

function resize() {
  const dpr = Math.min(devicePixelRatio || 1, 2);
  canvas.width = Math.floor(innerWidth * dpr);
  canvas.height = Math.floor(innerHeight * dpr);
  // integer scale so pixels stay square; aim for ~15 tiles across
  scale = Math.max(1, Math.round(canvas.width / (15 * TILE)));
  viewW = Math.ceil(canvas.width / scale);
  viewH = Math.ceil(canvas.height / scale);
  if (fboTex) gl.deleteTexture(fboTex);
  fboTex = texture(null, { data: null, w: viewW, h: viewH });
  gl.bindTexture(gl.TEXTURE_2D, fboTex);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, viewW, viewH, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
  if (!fbo) fbo = gl.createFramebuffer();
  gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
  gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, fboTex, 0);
  gl.bindFramebuffer(gl.FRAMEBUFFER, null);
}

// ------------------------------------------------------------------ input
addEventListener("keydown", e => {
  if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", " "].includes(e.key)) e.preventDefault();
  if (e.repeat) { keys.add(e.key.toLowerCase()); return; }
  keys.add(e.key.toLowerCase());
  music.unlock();
  const k = e.key.toLowerCase();
  if (mode === "battle") { battle.key(k); return; }
  if (k === " " || k === "enter" || k === "z") interact();
  if (k === "m") document.getElementById("minimap").classList.toggle("hidden");
  if (k === "t") { tod = (tod + 1) % 4; showPlaque(["Dawn", "Day", "Dusk", "Night"][tod], ""); music.setTod(tod); }
  if (k === "n") { sceneIndex++; loadScene(sceneIndex); }
});
addEventListener("keyup", e => keys.delete(e.key.toLowerCase()));
document.getElementById("sound").addEventListener("click", e => {
  music.unlock();
  const on = music.toggle();
  e.currentTarget.classList.toggle("on", on);
  e.currentTarget.blur();
});

function heldDir() {
  if (keys.has("arrowup") || keys.has("w")) return "up";
  if (keys.has("arrowdown") || keys.has("s")) return "down";
  if (keys.has("arrowleft") || keys.has("a")) return "left";
  if (keys.has("arrowright") || keys.has("d")) return "right";
  return null;
}
const DELTA = { up: [0, -1], down: [0, 1], left: [-1, 0], right: [1, 0] };

function interact() {
  if (mode === "dialog") { nextDialog(); return; }
  const [dx, dy] = DELTA[player.dir];
  const tx = player.x + dx, ty = player.y + dy;
  const key = `${ty},${tx}`;
  const here = `${player.y},${player.x}`;
  const obj = world.objects.get(key);
  if (obj && obj.kind === "rival") {
    if (rivalBeaten) { say(["Konrad: Fine. You got lucky.", "Konrad: I'll be at the Tvangsburg gym before you've learned to hold a lead."]); return; }
    const lv = party[0].level;
    say(["Konrad: There you are. I've been waiting an hour.",
         "Konrad: The Tvangsburg gym won't take either of us until we've earned it.",
         "Konrad: So let's see who he looks at first."], () => {
      mode = "battle";
      music.trainerBattle();
      battle.startTrainer(party, { name: "Konrad", lose: "Konrad: ...Again. Tomorrow. Same place.",
        team: [makeMon("Riolu", ["Fighting"], lv + 4, "rare"), makeMon("Houndour", ["Dark", "Fire"], lv + 5, "uncommon")] }, bag);
    });
    return;
  }
  if (obj && obj.kind === "door") { say([obj.note]); return; }
  if (obj) {
    if (obj.kind === "cut_tree") say(["A thin tree grows across the trail.", "You push through and snap it back. The way is open."], () => removeObject(key, tx, ty));
    else say(["A mossy standing stone. Someone has scratched a tally into it.", "It shifts when you lean on it and rolls aside."], () => removeObject(key, tx, ty));
    return;
  }
  for (const k of [key, here]) {
    if (world.hidden.has(k)) {
      const item = world.hidden.get(k);
      world.hidden.delete(k);
      music.sfx("item");
      say([`You found ${prettyItem(item)} hidden in the undergrowth.`]);
      return;
    }
  }
}

function removeObject(key, x, y) {
  const o = world.objects.get(key);
  o.sprite.alpha = 0;
  world.objects.delete(key);
  world.blocked[y * world.w + x] = 0;
  burst(x * TILE + 16, y * TILE + 12, o.kind === "cut_tree" ? [0.55, 0.38, 0.2] : [0.6, 0.58, 0.55], 18);
}

const prettyItem = s => {
  const n = s.replace("ITEM_", "").toLowerCase().split("_").map(w => w[0].toUpperCase() + w.slice(1)).join(" ");
  return /^[AEIOU]/.test(n) ? `an ${n}` : `a ${n}`;
};

// ------------------------------------------------------------------ dialog / ui
let plaqueTimer;
function showPlaque(title, sub) {
  const el = document.getElementById("plaque");
  document.getElementById("plaque-title").textContent = title;
  document.getElementById("plaque-sub").textContent = sub || "";
  el.classList.remove("hidden");
  clearTimeout(plaqueTimer);
  plaqueTimer = setTimeout(() => el.classList.add("hidden"), sub && sub.length > 60 ? 6500 : 3200);
}
let dialogDone = null;
function say(lines, done) {
  dialogQueue = lines.slice();
  dialogDone = done || null;
  mode = "dialog";
  nextDialog();
}
function nextDialog() {
  const el = document.getElementById("dialog");
  if (!dialogQueue.length) {
    el.classList.add("hidden");
    mode = "walk";
    const d = dialogDone; dialogDone = null;
    if (d) d();
    return;
  }
  document.getElementById("dialog-text").textContent = dialogQueue.shift();
  el.classList.remove("hidden");
}

// ------------------------------------------------------------------ encounters
function maybeEncounter() {
  if (world.cls(player.x, player.y) !== "t") return;
  if (Math.random() > 0.09) return;
  if (!party.some(m => m.hp > 0)) return;
  const enc = world.scene.encounters || {};
  const area = world.areaAt(player.x, player.y);
  const table = (area && enc.areas && enc.areas[area.area.id]) || enc.route || [];
  if (!table.length) return;
  let total = table.reduce((a, t) => a + t.weight, 0), r = Math.random() * total, pick = table[0];
  for (const t of table) { r -= t.weight; if (r <= 0) { pick = t; break; } }
  const lv = pick.levels[0] + Math.floor(Math.random() * (pick.levels[1] - pick.levels[0] + 1));
  const shiny = Math.random() < (1 / 512) * (pick.shiny_boost || 1);
  mode = "battle";
  music.battle(pick.rarity);
  battle.startWild(party, makeMon(pick.name, pick.types, lv, pick.rarity, shiny), area && area.name, bag);
}

// ------------------------------------------------------------------ movement
function update(dt) {
  if (mode !== "walk") { player.moving = false; return; }
  if (player.moving) {
    const run = keys.has("shift");
    player.t += dt / (run ? RUN_TIME : STEP_TIME);
    const t = Math.min(1, player.t);
    player.px = (player.from[0] + (player.to[0] - player.from[0]) * t) * TILE;
    player.py = (player.from[1] + (player.to[1] - player.from[1]) * t) * TILE;
    player.hop = player.ledge ? Math.sin(t * Math.PI) * 14 : 0;
    player.frame += dt * (run ? 15 : 10);
    if (t >= 1) {
      player.moving = false;
      player.x = player.to[0]; player.y = player.to[1];
      onStep();
    }
  }
  if (!player.moving && mode === "walk") {
    const d = heldDir();
    if (d) {
      player.dir = d;
      const [dx, dy] = DELTA[d];
      let nx = player.x + dx, ny = player.y + dy;
      // walking off the map edge through an exit leads to the next route
      if (nx < 0 || ny < 0 || nx >= world.w || ny >= world.h) {
        const side = nx < 0 ? "west" : nx >= world.w ? "east" : ny < 0 ? "north" : "south";
        const opposite = { north: "south", south: "north", east: "west", west: "east" }[side];
        sceneIndex++;
        loadScene(sceneIndex, opposite);
        return;
      }
      const c = world.cls(nx, ny);
      let ledge = false;
      if (c === "s") {
        if (d === "down" && !world.isBlocked(nx, ny + 1)) { ny += 1; ledge = true; } else return;
      } else if (world.isBlocked(nx, ny)) return;
      player.from = [player.x, player.y];
      player.to = [nx, ny];
      player.t = 0;
      player.ledge = ledge;
      player.moving = true;
      if (ledge) music.sfx("hop");
    } else player.frame = 0;
  }
}

function onStep() {
  player.steps++;
  const c = world.cls(player.x, player.y);
  if (c === "t") {
    burst(player.px + 16, player.py + 26, [0.35, 0.5, 0.2], 5, 0.5);
    music.sfx("grass");
  }
  const area = world.areaAt(player.x, player.y);
  if (area !== currentArea) {
    currentArea = area;
    if (area) { showPlaque(area.name, area.area.description || ""); music.setArea(area); }
    else music.setArea(null);
  }
  maybeEncounter();
}

// ------------------------------------------------------------------ particles
function burst(x, y, col, n, speed = 1) {
  for (let i = 0; i < n; i++) {
    particles.push({ x, y, vx: (Math.random() - 0.5) * 50 * speed, vy: -Math.random() * 60 * speed, life: 0.5 + Math.random() * 0.5,
      age: 0, size: 2, col, grav: 140, kind: "bit" });
  }
}
function spawnAmbient(dt) {
  const n = particles.length;
  // falling leaves
  if (n < 140 && Math.random() < dt * 9) {
    const palette = [[0.85, 0.45, 0.18], [0.7, 0.32, 0.14], [0.9, 0.62, 0.25], [0.55, 0.36, 0.18]];
    particles.push({ x: cam.x + Math.random() * (viewW + 120) - 60, y: cam.y - 10, vx: 8 + Math.random() * 10, vy: 16 + Math.random() * 14,
      life: 12, age: 0, size: Math.random() < 0.6 ? 2 : 3, col: palette[Math.floor(Math.random() * palette.length)], grav: 0, kind: "leaf",
      phase: Math.random() * 6.28 });
  }
  // chimney smoke from cottages on screen
  for (const hz of world.houses || []) {
    const [hx, hy] = hz.chimney;
    if (hx < cam.x - 50 || hx > cam.x + viewW + 50 || hy < cam.y - 80 || hy > cam.y + viewH + 50) continue;
    if (Math.random() < dt * 5) particles.push({ x: hx + (Math.random() - 0.5) * 4, y: hy, vx: 4 + Math.random() * 3, vy: -9 - Math.random() * 5,
      life: 3.5, age: 0, size: 2 + Math.floor(Math.random() * 3), col: [0.62, 0.58, 0.54], grav: -1, kind: "smoke", phase: Math.random() * 6 });
  }
  // dust motes / fireflies
  if (n < 160 && Math.random() < dt * (tod >= 2 ? 6 : 3)) {
    particles.push({ x: cam.x + Math.random() * viewW, y: cam.y + Math.random() * viewH, vx: (Math.random() - 0.5) * 4, vy: -2 - Math.random() * 3,
      life: 4 + Math.random() * 4, age: 0, size: 1, col: tod >= 2 ? [1.0, 0.85, 0.4] : [1, 0.95, 0.8], grav: 0,
      kind: tod >= 2 ? "firefly" : "mote", phase: Math.random() * 6.28 });
  }
}
function updateParticles(dt, time) {
  spawnAmbient(dt);
  for (const p of particles) {
    p.age += dt;
    if (p.kind === "leaf") { p.x += (p.vx + Math.sin(time * 2 + p.phase) * 16) * dt; p.y += p.vy * dt; }
    else if (p.kind === "smoke") { p.x += (p.vx + Math.sin(time * 1.3 + p.phase) * 3) * dt; p.y += p.vy * dt; if (p.age > 1.2 && p.size < 5 && Math.random() < dt) p.size++; }
    else if (p.kind === "firefly" || p.kind === "mote") { p.x += (p.vx + Math.sin(time + p.phase) * 5) * dt; p.y += (p.vy + Math.cos(time * 0.8 + p.phase) * 4) * dt; }
    else { p.vy += p.grav * dt; p.x += p.vx * dt; p.y += p.vy * dt; }
  }
  particles = particles.filter(p => p.age < p.life && p.y < cam.y + viewH + 40);
}

// ------------------------------------------------------------------ minimap
function drawMinimap() {
  const mm = document.getElementById("minimap");
  const s = 3;
  mm.width = world.w * s; mm.height = world.h * s;
  mm.style.width = `${world.w * s}px`; mm.style.height = `${world.h * s}px`;
  const ctx = mm.getContext("2d");
  const col = { f: "#2c3a1e", g: "#7a8a48", p: "#b08a55", t: "#4f6a2a", w: "#3c5a70", r: "#6b5a4a", s: "#8a6a3a" };
  for (let y = 0; y < world.h; y++) for (let x = 0; x < world.w; x++) {
    ctx.fillStyle = col[world.cls(x, y)] || "#000";
    ctx.fillRect(x * s, y * s, s, s);
  }
  ctx.strokeStyle = "#e8b04a";
  for (const a of world.areas) {
    const cells = [...a.cells].map(k => k.split(",").map(Number));
    const ys = cells.map(c => c[0]), xs = cells.map(c => c[1]);
    ctx.strokeRect(Math.min(...xs) * s - 0.5, Math.min(...ys) * s - 0.5, (Math.max(...xs) - Math.min(...xs) + 1) * s, (Math.max(...ys) - Math.min(...ys) + 1) * s);
  }
  world.minimapBase = ctx.getImageData(0, 0, mm.width, mm.height);
}
function updateMinimap() {
  const mm = document.getElementById("minimap");
  if (mm.classList.contains("hidden")) return;
  const ctx = mm.getContext("2d");
  ctx.putImageData(world.minimapBase, 0, 0);
  ctx.fillStyle = "#fff";
  ctx.fillRect(player.x * 3 - 1, player.y * 3 - 1, 5, 5);
}

// ------------------------------------------------------------------ render
const inst = new Float32Array(15 * 6000);
function pushSprite(n, x, y, w, h, src, sway, bright, alpha, flags, tint) {
  const o = n * 15;
  inst[o] = x; inst[o + 1] = y; inst[o + 2] = w; inst[o + 3] = h;
  inst[o + 4] = src[0]; inst[o + 5] = src[1]; inst[o + 6] = src[2]; inst[o + 7] = src[3];
  inst[o + 8] = sway; inst[o + 9] = bright; inst[o + 10] = alpha; inst[o + 11] = flags;
  inst[o + 12] = tint[0]; inst[o + 13] = tint[1]; inst[o + 14] = tint[2];
}

function render(time) {
  // camera eases toward the player and snaps to whole pixels
  const tx = player.px + 16 - viewW / 2, ty = player.py + 8 - viewH / 2;
  cam.x += (tx - cam.x) * 0.12; cam.y += (ty - cam.y) * 0.12;
  const maxX = world.w * TILE - viewW, maxY = world.h * TILE - viewH;
  cam.x = Math.max(0, Math.min(maxX, cam.x)); cam.y = Math.max(0, Math.min(maxY, cam.y));
  const cx = Math.round(cam.x), cy = Math.round(cam.y);

  gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
  gl.viewport(0, 0, viewW, viewH);
  gl.disable(gl.BLEND);

  // ground
  gl.useProgram(ground.p);
  gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, atlasTex);
  gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, cellTex);
  gl.uniform1i(ground.u.uAtlas, 0); gl.uniform1i(ground.u.uCells, 1);
  gl.uniform2f(ground.u.uAtlasSize, atlas.size[0], atlas.size[1]);
  gl.uniform2f(ground.u.uGrid, world.w, world.h);
  gl.uniform2f(ground.u.uCam, cx, cy);
  gl.uniform2f(ground.u.uView, viewW, viewH);
  gl.uniform1f(ground.u.uTime, time);
  gl.uniform1i(ground.u.uDebug, params.get("debug") === "ground" ? 1 : 0);
  setGroundRects();
  gl.bindVertexArray(quadVAO);
  gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);

  // sprites, y-sorted, only what's on screen
  const margin = 220;
  const vis = world.sprites.filter(s => s.alpha > 0 && s.x + s.w > cx - margin && s.x < cx + viewW + margin && s.y + s.h > cy - 40 && s.y < cy + viewH + margin);
  const dir = player.dir;
  const frames = atlas.groups[`player_${dir}`];
  const fi = player.moving ? 1 + (Math.floor(player.frame) % 8) : 0;
  const psrc = atlas.sprites[frames[fi]];
  const feetY = player.py + 30;
  // player shadow + sprite
  vis.push({ x: player.px + 6, y: player.py + 24, w: 20, h: 8, src: atlas.sprites.white_0, sway: 0, bright: 0.0, alpha: player.hop ? 0.18 : 0.28, flags: 0, sortY: feetY - 0.5, tint: [0, 0, 0] });
  vis.push({ x: player.px - 16, y: player.py - 30 - player.hop, w: 64, h: 64, src: psrc, sway: 0, bright: 1, alpha: 1, flags: 0, sortY: feetY, tint: [1, 1, 1] });
  // standing in tall grass: blades in front of the legs
  const inGrass = (cx2, cy2) => world.cls(cx2, cy2) === "t";
  const gx = Math.round(player.px / TILE), gy = Math.round(player.py / TILE);
  if (inGrass(gx, gy) && !player.hop) {
    const tops = world.grassTops;
    vis.push({ x: player.px - 4, y: player.py - 2, w: 32, h: 32, src: tops[0], sway: 1.2, bright: 0.95, alpha: 1, flags: 0, sortY: feetY + 1, tint: [1, 1, 1] });
    vis.push({ x: player.px + 6, y: player.py + 1, w: 32, h: 32, src: tops[1 % tops.length], sway: 1.4, bright: 0.9, alpha: 1, flags: 0, sortY: feetY + 1, tint: [1, 1, 1] });
  }
  vis.sort((a, b) => a.sortY - b.sortY);
  let n = 0;
  for (const s of vis) {
    if (n >= 5900) break;
    pushSprite(n++, s.x, s.y, s.w, s.h, s.src, s.sway, s.bright, s.alpha, s.flags, s.tint);
  }
  // particles on top
  const white = atlas.sprites.white_0;
  for (const p of particles) {
    if (n >= 5990) break;
    let a = Math.min(1, (p.life - p.age) * 2) * Math.min(1, p.age * 3);
    if (p.kind === "firefly") a *= 0.5 + 0.5 * Math.sin(time * 4 + p.phase);
    if (p.kind === "mote") a *= 0.35;
    if (p.kind === "smoke") a *= 0.45 * (1 - p.age / p.life);
    pushSprite(n++, Math.round(p.x), Math.round(p.y), p.size, p.size, [white[0], white[1], 1, 1], 0, 1, a, 0, p.col);
  }
  gl.enable(gl.BLEND);
  gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
  gl.useProgram(sprites.p);
  gl.uniform1i(sprites.u.uAtlas, 0);
  gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, atlasTex);
  gl.uniform2f(sprites.u.uCam, cx, cy);
  gl.uniform2f(sprites.u.uView, viewW, viewH);
  gl.uniform2f(sprites.u.uAtlasSize, atlas.size[0], atlas.size[1]);
  gl.uniform1f(sprites.u.uTime, time);
  gl.uniform4f(sprites.u.uPlayer, player.px + 16, feetY, 40, 0);
  gl.bindVertexArray(spriteVAO);
  gl.bindBuffer(gl.ARRAY_BUFFER, instBuf);
  gl.bufferData(gl.ARRAY_BUFFER, inst.subarray(0, n * 15), gl.DYNAMIC_DRAW);
  gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, n);

  // post to screen
  gl.bindFramebuffer(gl.FRAMEBUFFER, null);
  gl.viewport(0, 0, canvas.width, canvas.height);
  gl.disable(gl.BLEND);
  gl.useProgram(post.p);
  gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, fboTex);
  gl.uniform1i(post.u.uScene, 0);
  gl.uniform2f(post.u.uView, viewW, viewH);
  gl.uniform2f(post.u.uCam, cx, cy);
  gl.uniform1f(post.u.uTime, time);
  gl.uniform1f(post.u.uTod, tod);
  const lights = [{ x: player.px + 16, y: player.py + 10, r: 110, i: 1.0 }, ...world.lights];
  for (const p of particles) if (p.kind === "firefly") lights.push({ x: p.x, y: p.y, r: 22, i: 0.5 });
  const L = new Float32Array(64);
  let lc = 0;
  for (const l of lights) {
    const sx = l.x - cx, sy = l.y - cy;
    if (sx < -l.r || sy < -l.r || sx > viewW + l.r || sy > viewH + l.r) continue;
    // post samples flipped Y (gl origin bottom-left)
    L.set([sx, viewH - sy, l.r, l.i], lc * 4);
    if (++lc >= 16) break;
  }
  gl.uniform4fv(post.u.uLights, L);
  gl.uniform1i(post.u.uLightCount, lc);
  gl.bindVertexArray(quadVAO);
  gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
}

let rectsSet = false;
function setGroundRects() {
  if (rectsSet === world) return;
  const order = ["grass", "dirt", "tallgrass", "water", "grass_dark", "mud", "grass", "dirt_stony", "grass_flowers"];
  const rects = [], ranges = [];
  for (const g of order) {
    const names = atlas.groups[g];
    ranges.push(rects.length / 4, names.length);
    for (const nm of names) rects.push(...atlas.sprites[nm]);
  }
  while (ranges.length < 20) ranges.push(0, 1);
  gl.uniform4fv(ground.u.uRect, new Float32Array(rects.slice(0, 48 * 4)));
  gl.uniform2iv(ground.u.uRange, new Int32Array(ranges));
  rectsSet = world;
}

let last = performance.now();
function frame(now) {
  const dt = Math.min(0.05, (now - last) / 1000);
  last = now;
  if (paused) { requestAnimationFrame(frame); return; }
  const time = now / 1000;
  update(dt);
  updateParticles(dt, time);
  render(time);
  updateMinimap();
  music.tick(dt);
  requestAnimationFrame(frame);
}

window.arklarna = {
  player, world: () => world, keys, music, party, battle,
  setTod: t => { tod = t; music.setTod(t); },
  // jump somewhere for screenshots and tests; the camera follows instantly
  teleport(x, y, dir = "down") {
    Object.assign(player, { x, y, px: x * TILE, py: y * TILE, dir, moving: false });
    cam.x = player.px + 16 - viewW / 2; cam.y = player.py + 8 - viewH / 2;
  },
  load: i => loadScene(sceneIndex = i),
  // advance the game by a fixed step and draw (for frame-exact captures, no rAF needed)
  stepFrame(dt, time) { update(dt); updateParticles(dt, time); render(time); },
  pause(on) { paused = on; },
};
let paused = false;
init().catch(err => {
  console.error(err);
  document.getElementById("loading").textContent = "Something broke: see console";
});
