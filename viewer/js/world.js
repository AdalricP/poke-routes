// Build a renderable, walkable world from a generator scene.json.

export const MAT = { grass: 0, road: 1, tall: 2, water: 3, forest: 4, rock: 5, ledge: 6 };
const CLASS_MAT = { g: MAT.grass, p: MAT.road, t: MAT.tall, w: MAT.water, f: MAT.forest, r: MAT.rock, s: MAT.ledge };

// seeded rng so a scene always dresses the same way
export function rng(seed) {
  let s = seed >>> 0 || 1;
  return () => {
    s ^= s << 13; s >>>= 0; s ^= s >> 17; s ^= s << 5; s >>>= 0;
    return s / 4294967296;
  };
}

function smoothNoise(w, h, scale, rand) {
  const gw = Math.ceil(w / scale) + 2, gh = Math.ceil(h / scale) + 2;
  const g = Array.from({ length: gh * gw }, rand);
  return (x, y) => {
    const fx = x / scale, fy = y / scale, x0 = Math.floor(fx), y0 = Math.floor(fy);
    const tx = fx - x0, ty = fy - y0, sx = tx * tx * (3 - 2 * tx), sy = ty * ty * (3 - 2 * ty);
    const a = g[y0 * gw + x0], b = g[y0 * gw + x0 + 1], c = g[(y0 + 1) * gw + x0], d = g[(y0 + 1) * gw + x0 + 1];
    return (a * (1 - sx) + b * sx) * (1 - sy) + (c * (1 - sx) + d * sx) * sy;
  };
}

export class World {
  constructor(scene, atlas) {
    this.scene = scene;
    this.atlas = atlas;
    this.w = scene.width;
    this.h = scene.height;
    this.grid = scene.grid;
    this.rand = rng(scene.seed * 7919 + 13);
    this.blocked = new Uint8Array(this.w * this.h);
    this.sprites = [];      // static sprites: {x, y, w, h, src, sway, bright, alpha, flags, sortY, tint}
    this.objects = new Map(); // "y,x" -> interactive blocking object (cut tree, rock)
    this.hidden = new Map();  // "y,x" -> item name
    this.lights = [];         // world-space lights for night
    this.areas = [];
    this.buildCells();
    this.buildTrees();
    this.buildTallGrass();
    this.buildProps();
    this.buildEvents();
    this.buildAreas();
    this.buildHouses();
  }

  cls(x, y) {
    if (x < 0 || y < 0 || x >= this.w || y >= this.h) return "f";
    return this.grid[y][x];
  }

  pick(group) {
    const names = this.atlas.groups[group];
    return this.atlas.sprites[names[Math.floor(this.rand() * names.length)]];
  }

  buildCells() {
    const { w, h } = this;
    // forest depth: distance into the trees, for shadow pooling
    const depth = new Float32Array(w * h).fill(99);
    const q = [];
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      if (this.cls(x, y) !== "f") { depth[y * w + x] = 0; q.push([x, y]); }
    }
    for (let i = 0; i < q.length; i++) {
      const [x, y] = q[i];
      for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
        const nx = x + dx, ny = y + dy;
        if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue;
        if (depth[ny * w + nx] > depth[y * w + x] + 1) { depth[ny * w + nx] = depth[y * w + x] + 1; q.push([nx, ny]); }
      }
    }
    this.depth = depth;
    const data = new Uint8Array(w * h * 4);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const c = this.cls(x, y), i = (y * w + x) * 4;
      data[i] = CLASS_MAT[c] ?? 0;
      data[i + 1] = Math.min(255, Math.max(0, (depth[y * w + x] - 1) * 60));
      data[i + 2] = 0;
      data[i + 3] = 255;
      this.blocked[y * w + x] = c === "f" || c === "w" || c === "r" ? 1 : 0;
    }
    // closeness to the trees for open cells (leaf litter, shade)
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      if (this.cls(x, y) === "f") continue;
      let best = 9;
      for (let dy = -3; dy <= 3; dy++) for (let dx = -3; dx <= 3; dx++) {
        if (this.cls(x + dx, y + dy) === "f") best = Math.min(best, Math.hypot(dx, dy));
      }
      data[(y * w + x) * 4 + 2] = Math.max(0, Math.round(255 * (1 - (best - 1) / 3)));
    }
    this.cellData = data;
  }

  addSprite(src, x, y, opts = {}) {
    const s = {
      x, y, w: src[2], h: src[3], src,
      sway: opts.sway ?? 0, bright: opts.bright ?? 1, alpha: opts.alpha ?? 1, flags: opts.flags ?? 0,
      sortY: opts.sortY ?? y + src[3], tint: opts.tint ?? [1, 1, 1],
    };
    this.sprites.push(s);
    return s;
  }

  // Trees: Poisson-ish scatter over forest cells. Colour comes in groves (low-frequency
  // noise), so the mix of orange, brown, green and pale reads as natural variety.
  buildTrees() {
    const { w, h } = this;
    const colourN = smoothNoise(w, h, 9, this.rand);
    const sizeN = smoothNoise(w, h, 5, this.rand);
    const placed = [];
    const minD2 = 30 * 30;
    const cells = [];
    for (let y = 0; y < h + 3; y++) for (let x = -1; x <= w; x++) {
      if (this.cls(x, y) === "f") cells.push([x, y]);
    }
    for (let i = cells.length - 1; i > 0; i--) { const j = Math.floor(this.rand() * (i + 1)); [cells[i], cells[j]] = [cells[j], cells[i]]; }
    const bucket = new Map();
    const key = (x, y) => `${Math.floor(x / 64)},${Math.floor(y / 64)}`;
    for (const [cx, cy] of cells) {
      for (let attempt = 0; attempt < 2; attempt++) {
        const bx = cx * 32 + 6 + this.rand() * 20, by = cy * 32 + 14 + this.rand() * 16;
        let ok = true;
        for (let gx = -1; gx <= 1 && ok; gx++) for (let gy = -1; gy <= 1 && ok; gy++) {
          const list = bucket.get(`${Math.floor(bx / 64) + gx},${Math.floor(by / 64) + gy}`) || [];
          for (const [px, py] of list) if ((px - bx) ** 2 + (py - by) ** 2 < minD2) { ok = false; break; }
        }
        if (!ok) continue;
        const d = this.depth[Math.min(h - 1, Math.max(0, cy)) * w + Math.min(w - 1, Math.max(0, cx))] || 1;
        const cn = colourN(cx, cy) + (this.rand() - 0.5) * 0.25;
        const [g1, g2, g3] = { riverland: [0.5, 0.75, 0.9], meadow: [0.25, 0.5, 0.85] }[this.scene.biome] || [0.3, 0.52, 0.8];
        const colour = cn < g1 ? "green" : cn < g2 ? "brown" : cn < g3 ? "orange" : "pale";
        // biggest tree whose canopy stays over forest (half a cell of overhang allowed)
        const groups = sizeN(cx, cy) > 0.45 ? ["tree_big", "tree"] : ["tree"];
        let src = null;
        for (const g of groups) {
          const cand = this.pick(`${g}_${colour}`);
          if (this.canopyFits(cx, cy, cand[2], cand[3])) { src = cand; break; }
        }
        if (!src) continue;
        const tw = src[2], th = src[3];
        const shade = Math.max(0.72, 1 - (d - 1) * 0.06);
        this.addSprite(src, Math.round(bx - tw / 2), Math.round(by - th + 6), {
          sway: tw < 100 ? 1.2 : 1.6, bright: shade, flags: 1, sortY: by,
        });
        placed.push([bx, by]);
        const k = key(bx, by);
        if (!bucket.has(k)) bucket.set(k, []);
        bucket.get(k).push([bx, by]);
        break;
      }
    }
    // bushes along forest edges soften the line between trees and grass
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      if (this.cls(x, y) !== "f") continue;
      const edge = [[1, 0], [-1, 0], [0, 1]].some(([dx, dy]) => "gpt".includes(this.cls(x + dx, y + dy)));
      if (edge && this.rand() < 0.35) {
        const src = this.pick("plant");
        this.addSprite(src, x * 32 + Math.round(this.rand() * 4), y * 32 + 2, { sway: 0.6, sortY: y * 32 + 31 });
      }
    }
  }

  canopyFits(cx, cy, tw, th) {
    const r = Math.max(0, Math.floor((tw / 2 - 22) / 32));
    const up = Math.max(1, Math.floor((th * 0.7 - 16) / 32));
    for (let dx = -r; dx <= r; dx++) {
      for (let dy = 0; dy <= up; dy++) {
        // canopy edges taper, so only the middle column needs the full height
        if (Math.abs(dx) === r && r > 0 && dy > up - 1) continue;
        if (this.cls(cx + dx, cy - dy) !== "f") return false;
      }
    }
    return true;
  }

  buildTallGrass() {
    // the grass body is painted by the ground shader (organic edges); a few swaying
    // tufts in the interior give it motion without drawing the cell grid
    const tops = this.atlas.groups.tallgrass_top.map(n => this.atlas.sprites[n]);
    this.grassTops = tops;
    for (let y = 0; y < this.h; y++) for (let x = 0; x < this.w; x++) {
      if (this.cls(x, y) !== "t") continue;
      const inner = [[1, 0], [-1, 0], [0, 1], [0, -1]].every(([dx, dy]) => this.cls(x + dx, y + dy) === "t");
      if (!inner || this.rand() > 0.55) continue;
      const a = tops[Math.floor(this.rand() * tops.length)];
      this.addSprite(a, x * 32 + Math.round(this.rand() * 12 - 6), y * 32 - 4 + Math.round(this.rand() * 8), { sway: 1.6, sortY: y * 32 + 20 });
    }
  }

  buildProps() {
    const r = this.rand;
    const placeOn = (x, y, group, opts = {}) => {
      const src = this.pick(group);
      return this.addSprite(src, x * 32 + Math.round((32 - src[2]) / 2 + (r() - 0.5) * 8), y * 32 + 32 - src[3] - Math.round(r() * 4), {
        sortY: y * 32 + 28, ...opts,
      });
    };
    for (const p of this.scene.props) {
      if (p.kind === "great_tree") {
        const colour = ["orange", "brown", "pale"][Math.floor(r() * 3)];
        const src = this.pick(`tree_great_${colour}`);
        this.addSprite(src, p.x * 32 + 16 - src[2] / 2, p.y * 32 + 30 - src[3], { sway: 1.2, flags: 1, sortY: p.y * 32 + 30 });
      } else if (p.kind === "reeds") placeOn(p.x, p.y, "reeds", { sway: 1 });
      else if (p.kind === "flowers") placeOn(p.x, p.y, "plant", { sway: 0.6 });
      else if (p.kind === "standing_stone") { /* drawn as a rock_smash object */ }
    }
    // scatter: a little clutter makes ground feel lived-in; more of it near the trees
    for (let y = 1; y < this.h - 1; y++) for (let x = 1; x < this.w - 1; x++) {
      const c = this.cls(x, y);
      if (c === "w") {
        const shore = [[1, 0], [-1, 0], [0, 1], [0, -1]].some(([dx, dy]) => this.cls(x + dx, y + dy) !== "w");
        if (shore && r() < 0.28) {
          const src = this.pick("lily");
          this.addSprite(src, x * 32, y * 32, { sortY: y * 32 - 100, sway: 0.3 });
        }
        continue;
      }
      if (c !== "g") continue;
      const nearTrees = [[1, 0], [-1, 0], [0, 1], [0, -1]].filter(([dx, dy]) => this.cls(x + dx, y + dy) === "f").length;
      const roll = r();
      if (nearTrees && roll < 0.05) { placeOn(x, y, "mushrooms"); this.blocked[y * this.w + x] = 0; }
      else if (nearTrees && roll < 0.075) { placeOn(x, y, "stump"); this.blocked[y * this.w + x] = 1; }
      else if (roll < 0.012) { placeOn(x, y, "boulder"); this.blocked[y * this.w + x] = 1; }
      else if (roll < 0.03) placeOn(x, y, "plant", { sway: 0.5 });
      else if (nearTrees >= 2 && roll < 0.04) {
        const src = this.pick("log");
        if (this.cls(x + 1, y) === "g") {
          this.addSprite(src, x * 32, y * 32, { sortY: y * 32 + 30 });
          this.blocked[y * this.w + x] = 1; this.blocked[y * this.w + x + 1] = 1;
        }
      }
    }
    // rock cells: piled boulders read as a stone wall
    for (let y = 0; y < this.h; y++) for (let x = 0; x < this.w; x++) {
      if (this.cls(x, y) === "r") placeOn(x, y, "boulder", { bright: 0.85 + r() * 0.2 });
    }
  }

  buildEvents() {
    for (const e of this.scene.events) {
      const k = `${e.y},${e.x}`;
      if (e.kind === "hidden_item") this.hidden.set(k, e.item);
      if (e.kind === "cut_tree") {
        const s = this.addSprite(this.atlas.sprites[this.atlas.groups.pine[0]], e.x * 32 - 16, e.y * 32 - 34, { sortY: e.y * 32 + 30, sway: 0.8, flags: 1 });
        this.objects.set(k, { kind: "cut_tree", sprite: s });
        this.blocked[e.y * this.w + e.x] = 1;
      }
      if (e.kind === "rock_smash") {
        const src = this.atlas.sprites[this.atlas.groups.boulder[1]];
        const s = this.addSprite(src, e.x * 32, e.y * 32 + 32 - src[3], { sortY: e.y * 32 + 30 });
        this.objects.set(k, { kind: "rock_smash", sprite: s });
        this.blocked[e.y * this.w + e.x] = 1;
      }
    }
  }

  buildAreas() {
    for (const z of this.scene.zones) {
      if (z.kind !== "special") continue;
      const set = new Set(z.cells.map(([y, x]) => `${y},${x}`));
      this.areas.push({ name: z.name, area: z.meta.area || {}, cells: set, center: z.meta.center });
      if (z.meta.center) {
        const [cy, cx] = z.meta.center;
        this.lights.push({ x: cx * 32 + 16, y: cy * 32 + 16, r: 150, i: 0.7 });
      }
    }
  }

  // a cottage or two in the clearings: rural Arklarna lives in the woods
  buildHouses() {
    this.houses = [];
    const want = this.scene.biome === "meadow" ? 2 : 1;
    const zones = this.scene.zones.filter(z => z.kind === "clearing").concat(this.scene.zones.filter(z => z.kind === "branch"));
    const onPath = new Set();
    for (const z of this.scene.zones) if (["main", "branch", "loop", "dead_end", "trail", "hidden"].includes(z.kind)) for (const [y, x] of z.cells) onPath.add(`${y},${x}`);
    const free = (x, y) => this.cls(x, y) === "g" && !this.blocked[y * this.w + x] && !onPath.has(`${y},${x}`);
    const notes = this.scene.houseNotes || [
      "A slate by the door: Gone to Pilsau for the amber fair. Back by the new moon.",
      "The door is barred from inside. Someone is singing, badly, about the Haff.",
      "Fresh scratches on the frame, too high for a dog. The shutters are nailed shut.",
      "A wreath of dried rowan hangs on the door. It's meant to keep things out.",
    ];
    for (const z of zones) {
      if (this.houses.length >= want) break;
      const cells = z.cells.slice().sort(() => this.rand() - 0.5);
      for (const [y, x] of cells) {
        let ok = y > 6 && x > 2 && x < this.w - 6 && y < this.h - 5;
        for (let dx = 0; dx < 4 && ok; dx++) for (let dy = -1; dy <= 0 && ok; dy++) ok = free(x + dx, y + dy);
        // a ring of open ground so the trees frame the cottage instead of burying it
        for (let dx = -1; dx <= 4 && ok; dx++) for (let dy = -2; dy <= 2 && ok; dy++) ok = this.cls(x + dx, y + dy) !== "f" && this.cls(x + dx, y + dy) !== "w";
        ok = ok && "gpt".includes(this.cls(x + 1, y + 1)) && "gpt".includes(this.cls(x + 2, y + 1));
        if (!ok) continue;
        const src = this.pick("house");
        this.addSprite(src, x * 32 - 16, (y + 1) * 32 - src[3], { sortY: (y + 1) * 32 - 2 });
        for (let dx = 0; dx < 4; dx++) for (let dy = -1; dy <= 0; dy++) this.blocked[(y + dy) * this.w + x + dx] = 1;
        const note = notes[this.houses.length % notes.length];
        this.objects.set(`${y},${x + 1}`, { kind: "door", note, sprite: { alpha: 1 } });
        this.objects.set(`${y},${x + 2}`, { kind: "door", note, sprite: { alpha: 1 } });
        this.houses.push({ x, y, chimney: [x * 32 + 96, (y + 1) * 32 - src[3] + 18] });
        this.lights.push({ x: x * 32 + 64, y: y * 32 + 20, r: 90, i: 0.9 });
        break;
      }
    }
  }

  areaAt(x, y) {
    const k = `${y},${x}`;
    return this.areas.find(a => a.cells.has(k));
  }

  isBlocked(x, y) {
    if (x < 0 || y < 0 || x >= this.w || y >= this.h) return true;
    return this.blocked[y * this.w + x] === 1;
  }
}
