// A small turn-based battle: enough to make encounters real and to drive the music.

const T = ["Normal", "Fire", "Water", "Electric", "Grass", "Ice", "Fighting", "Poison", "Ground", "Flying", "Psychic", "Bug", "Rock", "Ghost", "Dragon", "Dark", "Steel", "Fairy"];
// attacker -> [super effective], [not very], [no effect]
const CHART = {
  Normal: [[], ["Rock", "Steel"], ["Ghost"]],
  Fire: [["Grass", "Ice", "Bug", "Steel"], ["Fire", "Water", "Rock", "Dragon"], []],
  Water: [["Fire", "Ground", "Rock"], ["Water", "Grass", "Dragon"], []],
  Electric: [["Water", "Flying"], ["Electric", "Grass", "Dragon"], ["Ground"]],
  Grass: [["Water", "Ground", "Rock"], ["Fire", "Grass", "Poison", "Flying", "Bug", "Dragon", "Steel"], []],
  Ice: [["Grass", "Ground", "Flying", "Dragon"], ["Fire", "Water", "Ice", "Steel"], []],
  Fighting: [["Normal", "Ice", "Rock", "Dark", "Steel"], ["Poison", "Flying", "Psychic", "Bug", "Fairy"], ["Ghost"]],
  Poison: [["Grass", "Fairy"], ["Poison", "Ground", "Rock", "Ghost"], ["Steel"]],
  Ground: [["Fire", "Electric", "Poison", "Rock", "Steel"], ["Grass", "Bug"], ["Flying"]],
  Flying: [["Grass", "Fighting", "Bug"], ["Electric", "Rock", "Steel"], []],
  Psychic: [["Fighting", "Poison"], ["Psychic", "Steel"], ["Dark"]],
  Bug: [["Grass", "Psychic", "Dark"], ["Fire", "Fighting", "Poison", "Flying", "Ghost", "Steel", "Fairy"], []],
  Rock: [["Fire", "Ice", "Flying", "Bug"], ["Fighting", "Ground", "Steel"], []],
  Ghost: [["Psychic", "Ghost"], ["Dark"], ["Normal"]],
  Dragon: [["Dragon"], ["Steel"], ["Fairy"]],
  Dark: [["Psychic", "Ghost"], ["Fighting", "Dark", "Fairy"], []],
  Steel: [["Ice", "Rock", "Fairy"], ["Fire", "Water", "Electric", "Steel"], []],
  Fairy: [["Fighting", "Dragon", "Dark"], ["Fire", "Poison", "Steel"], []],
};
function effectiveness(type, defTypes) {
  let m = 1;
  const [se, nve, imm] = CHART[type] || [[], [], []];
  for (const d of defTypes) {
    if (imm.includes(d)) m *= 0;
    else if (se.includes(d)) m *= 2;
    else if (nve.includes(d)) m *= 0.5;
  }
  return m;
}

// [name, power, minimum level]
const MOVES = {
  Normal: [["Tackle", 40, 1], ["Quick Attack", 40, 8], ["Headbutt", 70, 20], ["Body Slam", 85, 32]],
  Fire: [["Ember", 40, 1], ["Flame Wheel", 60, 14], ["Flamethrower", 90, 30]],
  Water: [["Water Gun", 40, 1], ["Water Pulse", 60, 15], ["Surf", 90, 32]],
  Electric: [["Thunder Shock", 40, 1], ["Spark", 65, 16], ["Thunderbolt", 90, 32]],
  Grass: [["Vine Whip", 45, 1], ["Razor Leaf", 55, 12], ["Energy Ball", 90, 32]],
  Ice: [["Powder Snow", 40, 1], ["Ice Fang", 65, 18], ["Ice Beam", 90, 34]],
  Fighting: [["Rock Smash", 40, 1], ["Brick Break", 75, 18], ["Close Combat", 120, 40]],
  Poison: [["Poison Sting", 35, 1], ["Poison Fang", 50, 12], ["Sludge Bomb", 90, 32]],
  Ground: [["Mud-Slap", 30, 1], ["Bulldoze", 60, 16], ["Earthquake", 100, 36]],
  Flying: [["Gust", 40, 1], ["Wing Attack", 60, 12], ["Air Slash", 75, 28]],
  Psychic: [["Confusion", 50, 1], ["Psybeam", 65, 18], ["Psychic", 90, 34]],
  Bug: [["Bug Bite", 40, 1], ["Signal Beam", 60, 15], ["X-Scissor", 80, 30]],
  Rock: [["Rock Throw", 50, 1], ["Rock Tomb", 60, 14], ["Rock Slide", 75, 28]],
  Ghost: [["Astonish", 30, 1], ["Hex", 65, 18], ["Shadow Ball", 80, 30]],
  Dragon: [["Dragon Breath", 60, 1], ["Dragon Claw", 80, 24], ["Dragon Pulse", 85, 36]],
  Dark: [["Bite", 60, 1], ["Feint Attack", 60, 14], ["Crunch", 80, 30]],
  Steel: [["Metal Claw", 50, 1], ["Iron Head", 80, 24], ["Flash Cannon", 80, 34]],
  Fairy: [["Fairy Wind", 40, 1], ["Draining Kiss", 50, 14], ["Moonblast", 95, 34]],
};
const BASE = { common: 52, uncommon: 66, rare: 80, very_rare: 92, legendary: 112, mythical: 105 };
const TYPE_COLOUR = {
  Normal: "#8a7f6a", Fire: "#b5532a", Water: "#3f6b8f", Electric: "#b89a2c", Grass: "#5e7d33", Ice: "#6f9aa3",
  Fighting: "#8a3a2a", Poison: "#7a4a7a", Ground: "#9a7a44", Flying: "#7484a8", Psychic: "#a84a6a", Bug: "#7a8a2a",
  Rock: "#8a7a4a", Ghost: "#4f4a72", Dragon: "#4a4a9a", Dark: "#3e3530", Steel: "#7a8088", Fairy: "#a87090",
};

const slug = n => ({ "Mr. Mime": "mr_mime", "Farfetch'd": "farfetchd", "Ho-Oh": "ho_oh" }[n] || n.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, ""));

export function makeMon(name, types, level, rarity = "common", shiny = false) {
  const b = BASE[rarity] ?? 60;
  const stat = k => Math.floor((2 * b * k * level) / 100) + 5;
  const maxHp = Math.floor(((2 * b * level) / 100 + level + 10) * 1.5);
  const pool = [];
  for (const t of types) for (const m of MOVES[t] || []) if (m[2] <= level) pool.push({ name: m[0], power: m[1], type: t });
  for (const m of MOVES.Normal) if (m[2] <= level) pool.push({ name: m[0], power: m[1], type: "Normal" });
  // strongest few, keeping at least one of each own type
  pool.sort((a, b2) => b2.power - a.power);
  const moves = [];
  for (const t of types) { const m = pool.find(p => p.type === t); if (m && !moves.includes(m)) moves.push(m); }
  for (const m of pool) if (moves.length < 4 && !moves.find(x => x.name === m.name)) moves.push(m);
  return {
    name, types, level, rarity, shiny, slug: slug(name),
    hp: maxHp, maxHp, atk: stat(1), def: stat(0.95), spd: stat(1) + (types.includes("Electric") || types.includes("Flying") ? 6 : 0),
    moves: moves.slice(0, 4), xp: 0,
  };
}

const xpFor = l => l * l * l;

export class Battle {
  constructor(music, onEnd) {
    this.music = music;
    this.onEnd = onEnd;
    this.el = document.getElementById("battle");
    this.busy = false;
    this.sel = 0;
    this.menu = "main";
  }

  get ally() { return this.party.find(m => m.hp > 0); }
  get foe() { return this.foes.find(m => m.hp > 0); }

  startWild(party, wild, areaName, bag) {
    this.kind = "wild";
    this.party = party; this.foes = [wild]; this.bag = bag;
    this.intro = [`${wild.shiny ? "A shimmering " : "A wild "}${wild.name} ${areaName ? `guards ${areaName}` : "bursts out of the grass"}!`];
    this.open();
  }

  startTrainer(party, trainer, bag) {
    this.kind = "trainer";
    this.trainer = trainer;
    this.party = party; this.foes = trainer.team; this.bag = bag;
    this.intro = [`${trainer.name} wants to fight.`, `${trainer.name} sends out ${trainer.team[0].name}.`];
    this.open();
  }

  open() {
    document.getElementById("plaque").classList.add("hidden");
    this.el.classList.remove("hidden");
    this.el.innerHTML = `
      <div class="arena">
        <div class="side foe"><div class="plate"></div><img class="mon" id="b-foe"></div>
        <div class="side ally"><div class="plate"></div><img class="mon" id="b-ally"></div>
        <div class="hpbox foe-box" id="b-foe-box"></div>
        <div class="hpbox ally-box" id="b-ally-box"></div>
      </div>
      <div class="bpanel"><p id="b-log"></p><div id="b-menu" class="bmenu"></div></div>`;
    this.refresh();
    this.say(this.intro, () => this.showMenu("main"));
  }

  refresh() {
    const set = (img, mon, side) => {
      img.src = `assets/mon/${mon.slug}/${side}${mon.shiny ? "_shiny" : ""}.png`;
      img.classList.toggle("fainted", mon.hp <= 0);
    };
    const f = this.foe || this.foes[this.foes.length - 1], a = this.ally || this.party[0];
    set(document.getElementById("b-foe"), f, "front");
    set(document.getElementById("b-ally"), a, "back");
    const box = (mon, showHp) => {
      const p = Math.max(0, mon.hp / mon.maxHp);
      const col = p > 0.5 ? "#6d8a3a" : p > 0.2 ? "#c49a2c" : "#a8442a";
      return `<div class="nm">${mon.name}<span>Lv${mon.level}</span></div>
        <div class="types">${mon.types.map(t => `<i style="background:${TYPE_COLOUR[t]}">${t}</i>`).join("")}</div>
        <div class="bar"><div style="width:${p * 100}%;background:${col}"></div></div>
        ${showHp ? `<div class="hpn">${Math.max(0, mon.hp)} / ${mon.maxHp}</div>` : ""}`;
    };
    document.getElementById("b-foe-box").innerHTML = box(f, false);
    document.getElementById("b-ally-box").innerHTML = box(a, true);
    if (this.kind === "trainer") {
      const pa = this.party.reduce((s, m) => s + Math.max(0, m.hp) / m.maxHp, 0) / this.party.length;
      const fo = this.foes.reduce((s, m) => s + Math.max(0, m.hp) / m.maxHp, 0) / this.foes.length;
      this.music.setAdvantage((pa - fo) * 1.6);
    }
  }

  say(lines, done) {
    this.busy = true;
    document.getElementById("b-menu").innerHTML = "";
    const log = document.getElementById("b-log");
    const q = lines.slice();
    const next = () => {
      if (!q.length) { this.busy = false; this.waiting = null; done && done(); return; }
      const line = q.shift();
      log.textContent = "";
      let i = 0;
      const type = () => {
        log.textContent = line.slice(0, ++i);
        if (i < line.length) this.typer = setTimeout(type, 14);
        else this.waiting = () => { this.waiting = null; next(); };
      };
      type();
    };
    next();
  }

  showMenu(kind) {
    this.menu = kind;
    this.sel = 0;
    const a = this.ally;
    let items;
    if (kind === "main") {
      document.getElementById("b-log").textContent = `What will ${a.name} do?`;
      items = [["Fight", "fight"], [`Potion ×${this.bag.potion}`, "potion"]];
      if (this.kind === "wild") items.push([`Ball ×${this.bag.ball}`, "ball"], ["Run", "run"]);
    } else {
      items = a.moves.map((m, i) => [`${m.name}<small style="background:${TYPE_COLOUR[m.type]}">${m.type} ${m.power}</small>`, `move:${i}`]);
      items.push(["Back", "back"]);
    }
    this.items = items;
    this.drawMenu();
  }

  drawMenu() {
    const el = document.getElementById("b-menu");
    el.innerHTML = this.items.map(([label], i) => `<button class="${i === this.sel ? "sel" : ""}" data-i="${i}">${label}</button>`).join("");
    el.querySelectorAll("button").forEach(b => b.addEventListener("click", () => { this.sel = +b.dataset.i; this.choose(); }));
  }

  key(k) {
    if (this.waiting && (k === " " || k === "enter" || k === "z")) { this.waiting(); return; }
    if (this.busy || !this.items) return;
    const n = this.items.length;
    if (k === "arrowdown" || k === "s" || k === "arrowright" || k === "d") { this.sel = (this.sel + 1) % n; this.drawMenu(); }
    if (k === "arrowup" || k === "w" || k === "arrowleft" || k === "a") { this.sel = (this.sel - 1 + n) % n; this.drawMenu(); }
    if (k === " " || k === "enter" || k === "z") this.choose();
    if ((k === "escape" || k === "x") && this.menu === "moves") this.showMenu("main");
  }

  choose() {
    const act = this.items[this.sel][1];
    if (act === "fight") return this.showMenu("moves");
    if (act === "back") return this.showMenu("main");
    if (act === "run") {
      if (Math.random() < 0.85) return this.say(["You back away slowly. It lets you go."], () => this.close("ran"));
      return this.turn(null, ["It cuts you off!"]);
    }
    if (act === "potion") {
      if (!this.bag.potion) return this.say(["The bag is empty."], () => this.showMenu("main"));
      this.bag.potion--;
      const a = this.ally, heal = Math.min(a.maxHp - a.hp, 25);
      a.hp += heal;
      this.refresh();
      return this.turn(null, [`${a.name} recovers ${heal} HP.`]);
    }
    if (act === "ball") {
      if (!this.bag.ball) return this.say(["No balls left."], () => this.showMenu("main"));
      this.bag.ball--;
      const f = this.foe;
      const rate = { common: 0.9, uncommon: 0.6, rare: 0.35, very_rare: 0.15 }[f.rarity] ?? 0.3;
      const chance = rate * (1 - (f.hp / f.maxHp) * 0.75);
      if (Math.random() < chance) {
        const msg = [`Gotcha! ${f.name} was caught.`];
        if (this.party.length < 6) { f.hp = Math.max(1, f.hp); this.party.push(f); msg.push(`${f.name} joins your party.`); }
        return this.say(msg, () => this.close("caught"));
      }
      return this.turn(null, [`${f.name} broke free!`]);
    }
    if (act.startsWith("move:")) return this.turn(this.ally.moves[+act.slice(5)], []);
  }

  attack(att, def, move) {
    const eff = effectiveness(move.type, def.types);
    const stab = att.types.includes(move.type) ? 1.5 : 1;
    const crit = Math.random() < 0.0625 ? 1.5 : 1;
    const dmg = eff === 0 ? 0 : Math.max(1, Math.floor(((((2 * att.level) / 5 + 2) * move.power * att.atk / def.def) / 50 + 2) * stab * eff * crit * (0.85 + Math.random() * 0.15)));
    def.hp = Math.max(0, def.hp - dmg);
    const lines = [`${att.name} uses ${move.name}!`];
    if (crit > 1 && dmg) lines.push("A critical hit!");
    if (eff > 1) lines.push("It's super effective!");
    else if (eff === 0) lines.push(`It doesn't affect ${def.name}.`);
    else if (eff < 1) lines.push("It's not very effective.");
    return lines;
  }

  // the foe plays to win: best expected damage, with a little unpredictability
  foeMove() {
    const f = this.foe, a = this.ally;
    let best = f.moves[0], bs = -1;
    for (const m of f.moves) {
      const s = m.power * effectiveness(m.type, a.types) * (f.types.includes(m.type) ? 1.5 : 1) * (0.8 + Math.random() * 0.4);
      if (s > bs) { bs = s; best = m; }
    }
    return best;
  }

  turn(move, pre) {
    this.busy = true;
    const steps = [];
    const a = this.ally, f = this.foe;
    const foeMove = this.foeMove();
    const order = move && a.spd >= f.spd ? ["ally", "foe"] : move ? ["foe", "ally"] : ["foe"];
    const run = (lines) => new Promise(res => this.say(lines, res));
    (async () => {
      if (pre.length) await run(pre);
      for (const who of order) {
        const att = who === "ally" ? this.ally : this.foe;
        const def = who === "ally" ? this.foe : this.ally;
        if (!att || !def || att.hp <= 0) continue;
        const lines = this.attack(att, def, who === "ally" ? move : foeMove);
        this.music.sfx("hit");
        this.flash(who === "ally" ? "b-foe" : "b-ally");
        this.refresh();
        await run(lines);
        if (def.hp <= 0) {
          this.music.sfx("faint");
          this.refresh();
          if (who === "ally") {
            const gain = Math.floor(def.level * (BASE[def.rarity] ?? 60) / 5 * (this.kind === "trainer" ? 3 : 1));
            await run([`${def.name} faints.`, ...this.giveXp(gain)]);
            if (!this.foe) {
              const end = this.kind === "trainer" ? [`You beat ${this.trainer.name}.`, this.trainer.lose] : [];
              return this.say(end.filter(Boolean), () => this.close("won"));
            }
            this.refresh();
            await run([`${this.trainer.name} sends out ${this.foe.name}.`]);
          } else {
            await run([`${def.name} faints.`]);
            if (!this.ally) return this.say(["You have no one left who can fight.", "You stumble back to the road."], () => this.close("lost"));
            this.refresh();
            await run([`Go, ${this.ally.name}!`]);
          }
          break;
        }
      }
      this.showMenu("main");
    })();
  }

  giveXp(gain) {
    const a = this.ally;
    a.xp += gain;
    const lines = [`${a.name} gains ${gain} experience.`];
    while (a.xp >= xpFor(a.level + 1) - xpFor(a.level) && a.level < 65) {
      a.xp -= xpFor(a.level + 1) - xpFor(a.level);
      const fresh = makeMon(a.name, a.types, a.level + 1, a.rarity, a.shiny);
      const hpGain = fresh.maxHp - a.maxHp;
      Object.assign(a, { level: fresh.level, maxHp: fresh.maxHp, hp: a.hp + hpGain, atk: fresh.atk, def: fresh.def, spd: fresh.spd, moves: fresh.moves });
      lines.push(`${a.name} grows to level ${a.level}.`);
    }
    return lines;
  }

  flash(id) {
    const el = document.getElementById(id);
    if (!el) return;
    el.classList.remove("hit"); void el.offsetWidth; el.classList.add("hit");
  }

  close(result) {
    clearTimeout(this.typer);
    this.el.classList.add("hidden");
    this.items = null;
    this.onEnd(result, this);
  }
}
