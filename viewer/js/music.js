// Procedural score for Arklarna: every route gets its own theme from its seed.
//
// Everything a route plays (calm walk, wild battle, trainer battle) is built
// from the same key, chord progression and motif, so the battle music is the
// route's song turned up rather than a different song. Trainer battles run two
// themes at once (the trainer's and the player's) over one shared progression
// and clock, and crossfade between them by who is winning. Because they share
// harmony and tempo, any pair of themes mixes cleanly.

const SCALES = {
  dorian: [0, 2, 3, 5, 7, 9, 10],
  aeolian: [0, 2, 3, 5, 7, 8, 10],
  phrygian: [0, 1, 3, 5, 7, 8, 10],
  ionian: [0, 2, 4, 5, 7, 9, 11],
  mixolydian: [0, 2, 4, 5, 7, 9, 10],
};
const PROGRESSIONS = [
  [0, 5, 2, 6], [0, 3, 5, 4], [0, 5, 3, 4], [0, 6, 5, 6], [0, 2, 3, 4], [0, 3, 0, 4], [5, 3, 0, 4],
];

function rng(seed) {
  let s = (seed >>> 0) || 1;
  return () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
}
const mtof = m => 440 * Math.pow(2, (m - 69) / 12);

// ------------------------------------------------------------------ themes
export function makeTheme(seed, mood = "calm") {
  const r = rng(seed * 2654435761);
  const modes = mood === "cave" ? ["phrygian", "aeolian"] : ["dorian", "aeolian", "dorian", "mixolydian"];
  const mode = modes[Math.floor(r() * modes.length)];
  const root = 45 + Math.floor(r() * 7);              // A2..D#3
  const prog = PROGRESSIONS[Math.floor(r() * PROGRESSIONS.length)];
  const tempo = mood === "cave" ? 58 + r() * 8 : 70 + r() * 12;
  return { seed, mood, mode, scale: SCALES[mode], root, prog, tempo, motif: makeMotif(r), counter: makeMotif(r) };
}

// A motif: 2 bars of 16th-steps -> scale degrees.
function makeMotif(r) {
  const rhythms = [
    [0, 4, 6, 8, 12, 16, 20, 24],
    [0, 3, 4, 8, 10, 12, 16, 24, 28],
    [0, 6, 8, 12, 14, 16, 22, 24],
    [0, 2, 4, 8, 16, 18, 20, 24],
    [0, 8, 12, 16, 20, 22, 24],
  ];
  const hits = rhythms[Math.floor(r() * rhythms.length)];
  let deg = [0, 2, 4][Math.floor(r() * 3)];
  const notes = [];
  for (let i = 0; i < hits.length; i++) {
    const step = r() < 0.7 ? (r() < 0.5 ? 1 : -1) : (r() < 0.5 ? 2 : -2);
    deg = Math.max(-2, Math.min(9, deg + (i === 0 ? 0 : step)));
    const nextHit = hits[i + 1] ?? 32;
    notes.push({ at: hits[i], deg, len: nextHit - hits[i] });
  }
  return notes;
}

function degToMidi(theme, deg, octave = 0) {
  const sc = theme.scale;
  const o = Math.floor(deg / sc.length);
  const d = ((deg % sc.length) + sc.length) % sc.length;
  return theme.root + 12 * (o + octave) + sc[d];
}
function chordTones(theme, bar) {
  const degree = theme.prog[bar % theme.prog.length];
  return [degree, degree + 2, degree + 4];
}
// snap a melody degree to the nearest chord tone (used on strong beats)
function snap(deg, tones) {
  let best = deg, bd = 99;
  for (const t of tones) for (const o of [-7, 0, 7]) {
    const c = t + o;
    if (Math.abs(c - deg) < bd) { bd = Math.abs(c - deg); best = c; }
  }
  return best;
}

// ------------------------------------------------------------------ synth voices
class Synth {
  constructor(ctx) {
    this.ctx = ctx;
    this.noise = ctx.createBuffer(1, ctx.sampleRate, ctx.sampleRate);
    const d = this.noise.getChannelData(0);
    for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
  }
  env(g, t, a, peak, decay, sustain, rel, dur) {
    const s = Math.max(0.0001, peak * sustain);
    g.gain.setValueAtTime(0.0001, t);
    g.gain.linearRampToValueAtTime(peak, t + a);
    g.gain.exponentialRampToValueAtTime(s, t + a + decay);
    g.gain.setValueAtTime(s, Math.max(t + a + decay, t + dur));
    g.gain.exponentialRampToValueAtTime(0.0001, Math.max(t + a + decay, t + dur) + rel);
  }
  osc(type, f, t, dur, dest, detune = 0) {
    const o = this.ctx.createOscillator();
    o.type = type; o.frequency.setValueAtTime(f, t); o.detune.value = detune;
    o.connect(dest); o.start(t); o.stop(t + dur + 0.05);
    return o;
  }
  // plucked harp / lute: bright attack, fast decay, lowpass closing
  pluck(m, t, vel, dest, dur = 1.2) {
    const f = mtof(m), g = this.ctx.createGain(), lp = this.ctx.createBiquadFilter();
    lp.type = "lowpass"; lp.frequency.setValueAtTime(Math.min(16000, f * 8), t); lp.frequency.exponentialRampToValueAtTime(f * 1.5, t + 0.4);
    g.connect(lp); lp.connect(dest);
    this.env(g, t, 0.004, 0.22 * vel, 0.35, 0.25, 0.6, 0.05);
    this.osc("triangle", f, t, dur, g);
    this.osc("sine", f * 2, t, dur * 0.5, g, 3);
  }
  // soft string pad: detuned saws through a slow filter
  pad(m, t, dur, vel, dest) {
    const f = mtof(m), g = this.ctx.createGain(), lp = this.ctx.createBiquadFilter();
    lp.type = "lowpass"; lp.frequency.value = 900; lp.Q.value = 0.4;
    g.connect(lp); lp.connect(dest);
    this.env(g, t, Math.min(1.2, dur * 0.4), 0.05 * vel, 0.5, 0.8, 1.4, dur);
    for (const dt of [-7, 0, 6]) this.osc("sawtooth", f, t, dur + 1.5, g, dt);
  }
  // glassy bell, slightly inharmonic
  bell(m, t, vel, dest) {
    const f = mtof(m), g = this.ctx.createGain();
    g.connect(dest);
    this.env(g, t, 0.002, 0.13 * vel, 1.4, 0.05, 1.2, 0.02);
    this.osc("sine", f, t, 3, g);
    const g2 = this.ctx.createGain(); g2.gain.value = 0.35; g2.connect(g);
    this.osc("sine", f * 2.76, t, 1.5, g2);
    const g3 = this.ctx.createGain(); g3.gain.value = 0.15; g3.connect(g);
    this.osc("sine", f * 5.4, t, 0.8, g3);
  }
  // breathy flute with delayed vibrato
  flute(m, t, dur, vel, dest) {
    const f = mtof(m), g = this.ctx.createGain();
    g.connect(dest);
    this.env(g, t, 0.06, 0.1 * vel, 0.2, 0.75, 0.25, dur);
    const o = this.osc("sine", f, t, dur + 0.3, g);
    const lfo = this.ctx.createOscillator(), lg = this.ctx.createGain();
    lfo.frequency.value = 5.2; lg.gain.setValueAtTime(0, t); lg.gain.linearRampToValueAtTime(f * 0.006, t + 0.35);
    lfo.connect(lg); lg.connect(o.frequency); lfo.start(t); lfo.stop(t + dur + 0.3);
    const n = this.ctx.createBufferSource(), ng = this.ctx.createGain(), bp = this.ctx.createBiquadFilter();
    n.buffer = this.noise; bp.type = "bandpass"; bp.frequency.value = f * 2; bp.Q.value = 2;
    n.connect(bp); bp.connect(ng); ng.connect(dest);
    this.env(ng, t, 0.04, 0.02 * vel, 0.2, 0.4, 0.2, dur);
    n.start(t); n.stop(t + dur + 0.3);
  }
  // reedy lead for battles (filtered square)
  lead(m, t, dur, vel, dest) {
    const f = mtof(m), g = this.ctx.createGain(), lp = this.ctx.createBiquadFilter();
    lp.type = "lowpass"; lp.frequency.setValueAtTime(f * 6, t); lp.frequency.exponentialRampToValueAtTime(f * 2.5, t + Math.max(0.05, dur));
    g.connect(lp); lp.connect(dest);
    this.env(g, t, 0.01, 0.07 * vel, 0.15, 0.6, 0.12, dur);
    this.osc("square", f, t, dur + 0.2, g);
    this.osc("sawtooth", f, t, dur + 0.2, g, 8);
  }
  // horn-ish brass swell for the player's theme
  horn(m, t, dur, vel, dest) {
    const f = mtof(m), g = this.ctx.createGain(), lp = this.ctx.createBiquadFilter();
    lp.type = "lowpass"; lp.frequency.setValueAtTime(f * 1.5, t); lp.frequency.linearRampToValueAtTime(f * 4, t + 0.15);
    g.connect(lp); lp.connect(dest);
    this.env(g, t, 0.05, 0.08 * vel, 0.3, 0.7, 0.2, dur);
    this.osc("sawtooth", f, t, dur + 0.25, g, -4);
    this.osc("sawtooth", f, t, dur + 0.25, g, 4);
  }
  bass(m, t, dur, vel, dest) {
    const f = mtof(m), g = this.ctx.createGain(), lp = this.ctx.createBiquadFilter();
    lp.type = "lowpass"; lp.frequency.value = 380;
    g.connect(lp); lp.connect(dest);
    this.env(g, t, 0.01, 0.2 * vel, 0.25, 0.55, 0.15, dur);
    this.osc("sawtooth", f, t, dur + 0.2, g);
    this.osc("sine", f / 2, t, dur + 0.2, g);
  }
  kick(t, vel, dest) {
    const o = this.ctx.createOscillator(), g = this.ctx.createGain();
    o.frequency.setValueAtTime(130, t); o.frequency.exponentialRampToValueAtTime(42, t + 0.14);
    g.gain.setValueAtTime(0.5 * vel, t); g.gain.exponentialRampToValueAtTime(0.001, t + 0.3);
    o.connect(g); g.connect(dest); o.start(t); o.stop(t + 0.35);
  }
  snare(t, vel, dest) {
    const n = this.ctx.createBufferSource(), g = this.ctx.createGain(), bp = this.ctx.createBiquadFilter();
    n.buffer = this.noise; bp.type = "bandpass"; bp.frequency.value = 1800; bp.Q.value = 0.7;
    n.connect(bp); bp.connect(g); g.connect(dest);
    g.gain.setValueAtTime(0.22 * vel, t); g.gain.exponentialRampToValueAtTime(0.001, t + 0.18);
    n.start(t); n.stop(t + 0.2);
  }
  hat(t, vel, dest) {
    const n = this.ctx.createBufferSource(), g = this.ctx.createGain(), hp = this.ctx.createBiquadFilter();
    n.buffer = this.noise; hp.type = "highpass"; hp.frequency.value = 7000;
    n.connect(hp); hp.connect(g); g.connect(dest);
    g.gain.setValueAtTime(0.06 * vel, t); g.gain.exponentialRampToValueAtTime(0.001, t + 0.05);
    n.start(t); n.stop(t + 0.06);
  }
  timpani(m, t, vel, dest) {
    const f = mtof(m), o = this.ctx.createOscillator(), g = this.ctx.createGain();
    o.type = "sine"; o.frequency.setValueAtTime(f * 1.5, t); o.frequency.exponentialRampToValueAtTime(f, t + 0.08);
    g.gain.setValueAtTime(0.35 * vel, t); g.gain.exponentialRampToValueAtTime(0.001, t + 1.2);
    o.connect(g); g.connect(dest); o.start(t); o.stop(t + 1.3);
  }
  rustle(t, dest) {
    const n = this.ctx.createBufferSource(), g = this.ctx.createGain(), bp = this.ctx.createBiquadFilter();
    n.buffer = this.noise; bp.type = "bandpass"; bp.frequency.value = 3200 + Math.random() * 1500; bp.Q.value = 1.2;
    n.connect(bp); bp.connect(g); g.connect(dest);
    g.gain.setValueAtTime(0.0001, t); g.gain.linearRampToValueAtTime(0.05, t + 0.03); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.18);
    n.playbackRate.value = 0.8 + Math.random() * 0.4;
    n.start(t, Math.random() * 0.5); n.stop(t + 0.2);
  }
}

function impulse(ctx, seconds = 3.2, decay = 2.6) {
  const len = Math.floor(ctx.sampleRate * seconds), buf = ctx.createBuffer(2, len, ctx.sampleRate);
  for (let c = 0; c < 2; c++) {
    const d = buf.getChannelData(c);
    for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, decay);
  }
  return buf;
}

// ------------------------------------------------------------------ parts
// Each part schedules whatever falls on one 16th step: (synth, theme, bar, step, time, dest, ctx).
const PARTS = {
  // walking
  harp(s, th, bar, step, t, dest, x) {
    if (step % 2) return;
    const tones = chordTones(th, bar);
    const pattern = [0, 1, 2, 3, 2, 1, 4, 2];
    const i = pattern[(step / 2) % 8];
    const deg = i < 3 ? tones[i] : i === 3 ? tones[0] + 7 : tones[1] + 7;
    s.pluck(degToMidi(th, deg, x.night ? 0 : 1), t, 0.65 + (step % 8 === 0 ? 0.2 : 0), dest);
  },
  pad(s, th, bar, step, t, dest, x) {
    if (step !== 0) return;
    for (const d of chordTones(th, bar)) s.pad(degToMidi(th, d, 1), t, x.barDur * 0.98, x.night ? 0.8 : 1, dest);
  },
  bassline(s, th, bar, step, t, dest, x) {
    if (step === 0 || (step === 8 && x.r() < 0.5)) s.bass(degToMidi(th, th.prog[bar % th.prog.length], 0), t, x.step * 7, 0.7, dest);
  },
  // 16-bar cycle: motif, motif up a third, counter-motif, motif to the cadence, then 8 bars of air
  melody(s, th, bar, step, t, dest, x) {
    const pb = bar % 16;
    if (pb >= 8) return;
    const motif = pb === 4 || pb === 5 ? th.counter : th.motif;
    const local = (pb % 2) * 16 + step;
    const shift = pb === 2 || pb === 3 ? 2 : 0;
    for (const n of motif) if (n.at === local) {
      let deg = n.deg + shift;
      if (local % 8 === 0) deg = snap(deg, chordTones(th, bar));
      if (pb === 7 && local >= 24) deg = snap(0, chordTones(th, bar));
      if (x.night) s.bell(degToMidi(th, deg, 2), t, 0.7, dest);
      else s.flute(degToMidi(th, deg, 2), t, n.len * x.step * 0.9, 0.9, dest);
    }
  },
  bells(s, th, bar, step, t, dest, x) {
    const pb = bar % 16;
    if ((pb < 8 && !x.area) || step % 4) return;
    if (x.r() < (x.area ? 0.4 : 0.3)) {
      const tones = chordTones(th, bar);
      s.bell(degToMidi(th, tones[Math.floor(x.r() * 3)] + 7, 2), t, 0.6, dest);
    }
  },
  drone(s, th, bar, step, t, dest, x) {
    if (step === 0 && bar % 2 === 0) s.pad(degToMidi(th, 0, 0), t, x.barDur * 2, 1.4, dest);
    if (step === 0 && bar % 4 === 3) s.timpani(degToMidi(th, 0, 0), t, 0.5, dest);
  },

  // battle: the same song, faster and armed
  drums(s, th, bar, step, t, dest, x) {
    const k = x.intensity;
    if (step % 8 === 0 || (k > 0.6 && step === 14)) s.kick(t, 0.9, dest);
    if (step % 8 === 4) s.snare(t, 0.8, dest);
    if (k > 0.3 && step % 2 === 0) s.hat(t, step % 4 ? 0.6 : 1, dest);
    if (k > 0.8 && step === 15 && bar % 2) s.snare(t, 0.5, dest);
  },
  ostinato(s, th, bar, step, t, dest, x) {
    if (step % 2) return;
    const root = th.prog[bar % th.prog.length];
    const pat = [0, 0, 7, 0, 0, 5, 7, 0];
    s.bass(degToMidi(th, root, 0) + pat[(step / 2) % 8], t, x.step * 1.6, 0.8, dest);
  },
  stabs(s, th, bar, step, t, dest, x) {
    if (step !== 6 && step !== 14 && step !== 0) return;
    for (const d of chordTones(th, bar)) s.pluck(degToMidi(th, d, 1), t, 0.9, dest, 0.4);
  },
  battleLead(s, th, bar, step, t, dest, x) {
    const pb = bar % 8;
    const motif = pb >= 4 && pb < 6 ? th.counter : th.motif;
    const local = (pb % 2) * 16 + step;
    for (const n of motif) if (n.at === local) {
      let deg = n.deg + (pb === 2 || pb === 3 ? 2 : 0);
      if (local % 8 === 0) deg = snap(deg, chordTones(th, bar));
      s.lead(degToMidi(th, deg, 2), t, n.len * x.step * 0.85, 1, dest);
    }
  },
  // the player's answer: same harmony, a brighter motif on horns
  playerLead(s, th, bar, step, t, dest, x) {
    const pb = bar % 8;
    const local = (pb % 2) * 16 + step;
    for (const n of x.playerMotif) if (n.at === local) {
      let deg = n.deg + (pb >= 4 ? 2 : 0);
      if (local % 8 === 0) deg = snap(deg, chordTones(th, bar));
      s.horn(degToMidi(th, deg, 1), t, n.len * x.step * 0.9, 1, dest);
    }
  },
  playerPad(s, th, bar, step, t, dest, x) {
    if (step !== 0) return;
    for (const d of chordTones(th, bar)) s.pad(degToMidi(th, d + 7, 1), t, x.barDur, 0.9, dest);
  },
  foeLow(s, th, bar, step, t, dest, x) {
    if (step % 4) return;
    const root = th.prog[bar % th.prog.length];
    s.horn(degToMidi(th, root, 0), t, x.step * 3, 0.9, dest);
    if (step === 0) s.timpani(degToMidi(th, root, -1), t, 0.7, dest);
  },
};

const ARRANGEMENTS = {
  calm: { parts: ["harp", "pad", "bassline", "melody", "bells"], tempo: 1 },
  cave: { parts: ["drone", "bells", "bassline"], tempo: 1 },
  wild: { parts: ["drums", "ostinato", "stabs", "battleLead", "pad"], tempo: 1.65 },
  trainer: { parts: ["drums", "ostinato", "stabs"], tempo: 1.75, foe: ["battleLead", "foeLow"], hero: ["playerLead", "playerPad"] },
};

// ------------------------------------------------------------------ engine
export class Music {
  constructor() {
    this.enabled = false;
    this.ctx = null;
    this.theme = null;
    this.arr = "calm";
    this.walkArr = "calm";
    this.bar = 0; this.step = 0; this.next = 0;
    this.advantage = 0;      // -1 foe winning .. 1 player winning
    this.shown = 0;
    this.night = false;
    this.inArea = false;
    this.intensity = 0.5;
  }

  unlock(given) {
    if (this.ctx) return;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC && !given) return;
    this.ctx = given || new AC();
    const c = this.ctx;
    this.master = c.createGain(); this.master.gain.value = 0;
    const comp = c.createDynamicsCompressor(); comp.threshold.value = -18; comp.ratio.value = 3;
    this.master.connect(comp); comp.connect(c.destination);
    this.reverb = c.createConvolver(); this.reverb.buffer = impulse(c);
    const wet = c.createGain(); wet.gain.value = 0.55;
    this.reverb.connect(wet); wet.connect(this.master);
    this.bus = {};
    for (const name of ["walk", "battle", "foe", "hero", "sfx"]) {
      const g = c.createGain(); g.gain.value = name === "walk" || name === "sfx" ? 1 : 0;
      const dry = c.createGain(); dry.gain.value = 0.7;
      const send = c.createGain(); send.gain.value = name === "sfx" ? 0.2 : 0.6;
      g.connect(dry); dry.connect(this.master); g.connect(send); send.connect(this.reverb);
      this.bus[name] = g;
    }
    this.synth = new Synth(c);
    this.playerMotif = makeMotif(rng(0xA2C1));
    this.next = c.currentTime + 0.1;
    if (!given) this.timer = setInterval(() => this.schedule(), 25);
    this.toggle(true);
  }

  // Render a stretch of a scene's music offline (for previews and tests).
  // mode: "calm" | "wild" | "trainer"; advantage curve for trainer: t -> -1..1
  static async render(scene, { mode = "calm", seconds = 20, advantage = null, night = false } = {}) {
    const rate = 44100;
    const ctx = new OfflineAudioContext(2, rate * seconds, rate);
    const m = new Music();
    m.unlock(ctx);
    m.master.gain.cancelScheduledValues(0);
    m.master.gain.value = 0.9;
    m.setScene(scene);
    m.night = night;
    if (mode === "wild") { m.arr = "wild"; m.intensity = 0.7; m.bus.walk.gain.value = 0; m.bus.battle.gain.value = 1; }
    if (mode === "trainer") { m.arr = "trainer"; m.intensity = 1; m.bus.walk.gain.value = 0; m.bus.battle.gain.value = 1; }
    m.next = 0.05;
    // schedule everything up front, stepping the crossfade as we go
    const end = seconds - 0.5;
    let t = 0;
    while (t < end) {
      if (mode === "trainer" && advantage) {
        const a = advantage(t / seconds), p = (a + 1) / 2;
        m.bus.hero.gain.setValueAtTime(0.15 + 0.85 * p, t);
        m.bus.foe.gain.setValueAtTime(0.15 + 0.85 * (1 - p), t);
      }
      while (m.next < t + 0.25) m.scheduleOne();
      t += 0.25;
    }
    const buf = await ctx.startRendering();
    return buf;
  }

  toggle(force) {
    if (!this.ctx) return false;
    this.enabled = force ?? !this.enabled;
    const t = this.ctx.currentTime;
    this.master.gain.cancelScheduledValues(t);
    this.master.gain.setTargetAtTime(this.enabled ? 0.9 : 0, t, 0.4);
    const btn = document.getElementById("sound");
    if (btn) btn.classList.toggle("on", this.enabled);
    return this.enabled;
  }

  setScene(scene) {
    this.theme = makeTheme(scene.seed + scene.name.length * 131, scene.biome === "cave" ? "cave" : "calm");
    this.walkArr = scene.biome === "cave" ? "cave" : "calm";
    if (this.arr === "calm" || this.arr === "cave") this.arr = this.walkArr;
    this.bar = 0; this.step = 0;
  }

  setArea(area) { this.inArea = !!area; }
  setTod(tod) { this.night = tod === 3; }

  fade(bus, to, time = 0.8) {
    if (!this.ctx) return;
    const g = this.bus[bus].gain, t = this.ctx.currentTime;
    g.cancelScheduledValues(t); g.setValueAtTime(g.value, t); g.linearRampToValueAtTime(to, t + time);
  }

  battle(rarity = "common") {
    this.arr = "wild";
    this.intensity = { common: 0.35, uncommon: 0.6, rare: 0.85, very_rare: 1 }[rarity] ?? 0.5;
    this.fade("walk", 0, 0.3); this.fade("battle", 1, 0.2);
    this.alignToBar();
  }

  trainerBattle() {
    this.arr = "trainer";
    this.intensity = 1;
    this.advantage = 0; this.shown = 0;
    this.fade("walk", 0, 0.3); this.fade("battle", 1, 0.2);
    this.fade("foe", 0.5, 0.2); this.fade("hero", 0.5, 0.2);
    this.alignToBar();
  }

  // -1 (trainer clearly winning) .. +1 (player clearly winning)
  setAdvantage(a) { this.advantage = Math.max(-1, Math.min(1, a)); }

  endBattle() {
    this.arr = this.walkArr;
    this.fade("battle", 0, 1.2); this.fade("foe", 0, 1.2); this.fade("hero", 0, 1.2); this.fade("walk", 1, 2.5);
  }

  alignToBar() { this.step = 0; this.bar = 0; if (this.ctx) this.next = this.ctx.currentTime + 0.05; }

  tick(dt) {
    if (!this.ctx || this.arr !== "trainer") return;
    // ease the crossfade so a single hit nudges rather than flips the music
    this.shown += (this.advantage - this.shown) * Math.min(1, dt * 0.8);
    const p = (this.shown + 1) / 2;
    const t = this.ctx.currentTime;
    this.bus.hero.gain.setTargetAtTime(0.15 + 0.85 * p, t, 0.1);
    this.bus.foe.gain.setTargetAtTime(0.15 + 0.85 * (1 - p), t, 0.1);
  }

  schedule() {
    if (!this.ctx || !this.theme) return;
    while (this.next < this.ctx.currentTime + 0.18) this.scheduleOne();
  }

  scheduleOne() {
    {
      const arr = ARRANGEMENTS[this.arr];
      const spb = 60 / (this.theme.tempo * arr.tempo) / 4;
      const x = { step: spb, barDur: spb * 16, night: this.night, area: this.inArea, r: Math.random, intensity: this.intensity, playerMotif: this.playerMotif };
      const busName = this.arr === "calm" || this.arr === "cave" ? "walk" : "battle";
      if (this.enabled) {
        for (const p of arr.parts) PARTS[p](this.synth, this.theme, this.bar, this.step, this.next, this.bus[busName], x);
        if (arr.foe) for (const p of arr.foe) PARTS[p](this.synth, this.theme, this.bar, this.step, this.next, this.bus.foe, x);
        if (arr.hero) for (const p of arr.hero) PARTS[p](this.synth, this.theme, this.bar, this.step, this.next, this.bus.hero, x);
      }
      this.next += spb;
      this.step = (this.step + 1) % 16;
      if (this.step === 0) this.bar++;
    }
  }

  sfx(name) {
    if (!this.ctx || !this.enabled) return;
    const t = this.ctx.currentTime + 0.01, s = this.synth, d = this.bus.sfx;
    if (name === "grass") s.rustle(t, d);
    if (name === "item") [0, 4, 7, 12].forEach((iv, i) => s.bell(72 + iv, t + i * 0.09, 0.9, d));
    if (name === "hop") { s.pluck(67, t, 0.6, d, 0.2); s.pluck(74, t + 0.08, 0.6, d, 0.2); }
    if (name === "hit") s.snare(t, 1.2, d);
    if (name === "faint") [7, 4, 0, -5].forEach((iv, i) => s.pluck(60 + iv, t + i * 0.14, 0.8, d, 0.5));
  }
}
