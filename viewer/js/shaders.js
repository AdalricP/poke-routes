// GLSL for the three passes: ground, sprites, post.

const NOISE = `
float hash(vec2 p) { p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }
float vnoise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1, 0)), f.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), f.x), f.y);
}
float fbm(vec2 p) {
  float a = 0.5, s = 0.0;
  for (int i = 0; i < 4; i++) { s += a * vnoise(p); p = p * 2.03 + 11.7; a *= 0.5; }
  return s;
}
`;

export const FULLSCREEN_VS = `#version 300 es
in vec2 aPos;
out vec2 vUV;
void main() { vUV = aPos * 0.5 + 0.5; gl_Position = vec4(aPos, 0.0, 1.0); }
`;

// Materials (cell texture R channel):
// 0 grass, 1 road, 2 tall-grass floor, 3 water, 4 forest floor, 5 rock floor, 6 ledge
export const GROUND_FS = `#version 300 es
precision highp float;
precision highp int;
uniform sampler2D uAtlas;
uniform sampler2D uCells;      // R material, G forest depth, B extra
uniform vec2 uAtlasSize;
uniform vec2 uGrid;            // cells
uniform vec2 uCam;             // world px of the view's top-left
uniform vec2 uView;            // view size in px
uniform float uTime;
uniform vec4 uRect[48];        // atlas rects of all ground variants
uniform ivec2 uRange[10];      // per material: first rect, count
uniform int uDebug;
in vec2 vUV;
out vec4 outColor;
${NOISE}

vec4 cellAt(vec2 wp) {
  ivec2 c = clamp(ivec2(floor(wp / 32.0)), ivec2(0), ivec2(uGrid) - 1);
  return texelFetch(uCells, c, 0);
}
int matAt(vec2 wp) { return int(cellAt(wp).r * 255.0 + 0.5); }
vec2 warp(vec2 wp) {
  vec2 big = vec2(fbm(wp * 0.03), fbm(wp * 0.03 + 31.7)) - 0.5;
  vec2 small = vec2(vnoise(wp * 0.16 + 7.0), vnoise(wp * 0.16 + 19.0)) - 0.5;
  return wp + big * 34.0 + small * 6.0;
}

// priority decides which side of a border draws the dark rim
int prio(int m) {
  if (m == 3) return 0;      // water lowest
  if (m == 1) return 1;      // road
  if (m == 5) return 2;      // rock floor
  if (m == 4) return 4;      // forest floor
  if (m == 2) return 5;      // tall grass floor
  return 3;                  // grass, ledge
}

vec4 tex4(int range, vec2 wp, vec2 cell) {
  ivec2 r = uRange[range];
  int n = max(r.y, 1);
  int idx = r.x + int(floor(hash(cell + float(range) * 17.13) * float(n))) % n;
  vec4 rc = uRect[idx];
  vec2 uv = (rc.xy + mod(floor(wp), rc.zw) + 0.5) / uAtlasSize;
  return texture(uAtlas, uv);
}
vec3 tex(int range, vec2 wp, vec2 cell) { return tex4(range, wp, cell).rgb; }

vec3 water(vec2 wp) {
  float t = uTime;
  vec2 q = wp + vec2(sin(wp.y * 0.09 + t * 1.3) * 2.0, cos(wp.x * 0.07 + t) * 1.5);
  vec3 c = tex(3, q, floor(wp / 64.0));
  c = mix(c, vec3(0.12, 0.22, 0.26), 0.35);
  float glint = step(0.985, vnoise(floor(wp / 2.0) * 0.7 + vec2(t * 0.8, -t * 0.5)));
  return c + glint * vec3(0.55, 0.5, 0.35);
}

void main() {
  vec2 wp = floor(uCam + vec2(gl_FragCoord.x, uView.y - gl_FragCoord.y));
  vec2 w = warp(wp);
  int m = matAt(w);
  vec2 cell = floor(w / 32.0);
  vec4 info = cellAt(wp);
  vec3 c;
  if (uDebug == 1) {
    int mm = int(info.r * 255.0 + 0.5);
    vec3 pal[7] = vec3[7](vec3(.4,.7,.3), vec3(.8,.6,.3), vec3(.1,.4,.1), vec3(.2,.3,.8), vec3(.05,.2,.05), vec3(.5,.5,.5), vec3(.9,.4,.1));
    outColor = vec4(pal[mm], 1.0); return;
  }

  if (m == 3) {
    c = water(wp);
    // foam just off the bank
    float shore = 0.0;
    for (int i = 0; i < 4; i++) {
      vec2 o = vec2(i == 0 ? 3.0 : i == 1 ? -3.0 : 0.0, i == 2 ? 3.0 : i == 3 ? -3.0 : 0.0);
      if (matAt(warp(wp + o)) != 3) shore = 1.0;
    }
    float foam = shore * step(0.45, vnoise(wp * 0.25 + uTime * 0.6));
    c = mix(c, vec3(0.78, 0.82, 0.74), foam * 0.55);
  } else if (m == 1) {
    float stony = step(0.72, fbm(cell * 0.35 + 3.1));
    c = stony > 0.5 ? tex(7, wp, cell) : tex(1, wp, cell);
    // pebbles and darker worn patches
    float peb = step(0.965, hash(floor(wp / 2.0) + 3.3));
    c = mix(c, vec3(0.62, 0.55, 0.46), peb * 0.8);
    c *= 0.9 + 0.12 * vnoise(wp * 0.07);
  } else if (m == 4) {
    c = tex(4, wp, cell) * vec3(0.62, 0.6, 0.5);
  } else if (m == 5) {
    c = tex(7, wp, cell) * vec3(0.8, 0.78, 0.76);
  } else if (m == 2) {
    // stagger each column so the blade tips don't line up into stripes
    vec2 gp = wp + vec2(0.0, floor(hash(vec2(floor(wp.x / 32.0), 7.0)) * 32.0));
    vec4 tg = tex4(2, gp, floor(gp / 32.0));
    c = mix(tex(4, wp, cell) * 0.7, tg.rgb, tg.a) * vec3(0.92, 0.95, 0.85);
  } else {
    c = tex(0, wp, cell);
    // fine speckle so one grass tile doesn't read as a repeating stamp
    float sp = hash(floor(wp / 2.0));
    c *= 0.94 + 0.1 * step(0.7, sp) + 0.04 * vnoise(wp * 0.11);
  }

  // dark rim where this material borders a lower one (LPC-style outline)
  if (m != 3) {
    int p = prio(m);
    float rim = 0.0;
    if (prio(matAt(warp(wp + vec2(0.0, 2.0)))) < p) rim = 1.0;
    if (prio(matAt(warp(wp + vec2(2.0, 0.0)))) < p || prio(matAt(warp(wp + vec2(-2.0, 0.0)))) < p) rim = max(rim, 0.7);
    if (prio(matAt(warp(wp + vec2(0.0, -2.0)))) < p) rim = max(rim, 0.5);
    c *= 1.0 - rim * 0.32;
    // muddy bank next to water
    float bank = 0.0;
    for (int i = 1; i <= 5; i++) {
      if (matAt(warp(wp + vec2(0.0, float(i)))) == 3 || matAt(warp(wp + vec2(float(i), 0.0))) == 3 ||
          matAt(warp(wp - vec2(float(i), 0.0))) == 3) { bank = 1.0 - float(i - 1) / 5.0; break; }
    }
    c = mix(c, vec3(0.33, 0.22, 0.14), bank * 0.75);
  }

  // ledge lip: a short earthen drop on the south edge of the cell
  if (int(info.r * 255.0 + 0.5) == 6) {
    float fy = mod(wp.y, 32.0);
    if (fy > 22.0) c = mix(vec3(0.36, 0.24, 0.15), vec3(0.22, 0.14, 0.09), (fy - 22.0) / 10.0);
    if (fy > 21.0 && fy <= 23.0) c = vec3(0.55, 0.42, 0.25);
  }

  // fallen leaves, thickest under and near the trees
  if (m != 3) {
    float near = max(info.b, info.g > 0.0 ? 1.0 : 0.0);
    vec2 lc = floor(wp / 3.0);
    float h1 = hash(lc + 9.1);
    float dens = near * 0.16 + 0.015;
    if (h1 < dens) {
      float pick = hash(lc + 2.7);
      vec3 leaf = pick < 0.33 ? vec3(0.78, 0.38, 0.14) : pick < 0.66 ? vec3(0.62, 0.3, 0.12) : vec3(0.86, 0.6, 0.24);
      // each leaf covers a little 2x2 or 3x2 shape
      vec2 f = mod(wp, 3.0);
      float shape = (f.y < 2.0 && f.x < (hash(lc) < 0.5 ? 2.0 : 3.0)) ? 1.0 : 0.0;
      c = mix(c, leaf, shape * 0.85);
    }
  }
  // large, soft colour drift: patches of dried grass and leaf litter
  float drift = fbm(wp * 0.006 + 7.0);
  c *= mix(vec3(1.0), vec3(1.12, 0.98, 0.72), smoothstep(0.45, 0.75, drift) * 0.6);
  // shadow pooling into the forest
  c *= 1.0 - info.g * 0.4;
  // dappled light through the canopy, banded to keep a painted feel
  float dap = fbm((wp + vec2(uTime * 7.0, uTime * 3.0)) * 0.018);
  dap = floor(dap * 4.0) / 4.0;
  c *= 0.86 + dap * 0.3;
  outColor = vec4(c, 1.0);
}
`;

export const SPRITE_VS = `#version 300 es
in vec2 aCorner;            // 0..1 quad corner
in vec4 aDst;               // world x, y (top-left), w, h
in vec4 aSrc;               // atlas x, y, w, h
in vec4 aParams;            // sway, brightness, alpha, flags (1 = tree fade, 2 = additive glow)
in vec3 aTint;
uniform vec2 uCam, uView, uAtlasSize;
uniform float uTime;
out vec2 vUV;
out vec4 vParams;
out vec3 vTint;
out vec2 vWorld;
void main() {
  vec2 local = aCorner * aDst.zw;
  float top = 1.0 - aCorner.y;
  float phase = aDst.x * 0.031 + aDst.y * 0.017;
  float sway = aParams.x * top * top * (sin(uTime * 1.6 + phase) + 0.45 * sin(uTime * 2.7 + phase * 1.7));
  vec2 world = aDst.xy + local + vec2(floor(sway + 0.5), 0.0);
  vWorld = world;
  vec2 screen = world - uCam;
  vec2 clip = screen / uView * 2.0 - 1.0;
  gl_Position = vec4(clip.x, -clip.y, 0.0, 1.0);
  vUV = (aSrc.xy + aCorner * aSrc.zw) / uAtlasSize;
  vParams = aParams;
  vTint = aTint;
}
`;

export const SPRITE_FS = `#version 300 es
precision highp float;
uniform sampler2D uAtlas;
uniform vec4 uPlayer;       // world x, y (feet), fade radius, unused
in vec2 vUV;
in vec4 vParams;
in vec3 vTint;
in vec2 vWorld;
out vec4 outColor;
float bayer(vec2 p) {
  int x = int(mod(p.x, 4.0)), y = int(mod(p.y, 4.0));
  int i = x + y * 4;
  int m[16] = int[16](0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5);
  return float(m[i]) / 16.0;
}
void main() {
  vec4 c = texture(uAtlas, vUV);
  if (c.a < 0.02) discard;
  c.rgb *= vTint * vParams.y;
  float a = c.a * vParams.z;
  // trees thin out (dithered) where they would hide the player
  if (vParams.w == 1.0) {
    vec2 d = (vWorld - uPlayer.xy + vec2(0.0, 26.0)) / vec2(26.0, 38.0);
    float k = 1.0 - smoothstep(0.6, 1.0, length(d));
    if (k > 0.0 && bayer(gl_FragCoord.xy) < k * 0.55) discard;
  }
  if (vParams.w == 2.0) { outColor = vec4(c.rgb * a, a * 0.0); return; }
  outColor = vec4(c.rgb * a, a);
}
`;

export const POST_FS = `#version 300 es
precision highp float;
uniform sampler2D uScene;
uniform vec2 uView;
uniform vec2 uCam;
uniform float uTime;
uniform float uTod;          // 0 dawn, 1 day, 2 dusk, 3 night
uniform vec4 uLights[16];    // screen-space x, y, radius, intensity
uniform int uLightCount;
in vec2 vUV;
out vec4 outColor;
${NOISE}

vec3 grade(vec3 c, float tod) {
  // storybook palette: pull toward warm browns, keep darks plum, lights parchment
  float l = dot(c, vec3(0.299, 0.587, 0.114));
  vec3 lo = vec3(0.13, 0.07, 0.08), mid = vec3(0.52, 0.34, 0.2), hi = vec3(0.98, 0.88, 0.66);
  vec3 ramp = l < 0.5 ? mix(lo, mid, l * 2.0) : mix(mid, hi, (l - 0.5) * 2.0);
  // tame greens toward olive
  float green = max(0.0, c.g - max(c.r, c.b));
  c.r += green * 0.35; c.b -= green * 0.1;
  c = mix(c, ramp, 0.2);
  vec3 tint = tod < 0.5 ? vec3(1.02, 0.92, 0.95) : tod < 1.5 ? vec3(1.06, 1.0, 0.88) : tod < 2.5 ? vec3(1.12, 0.86, 0.66) : vec3(0.36, 0.42, 0.8);
  float expo = tod < 2.5 ? 1.0 : 0.55;
  return c * tint * expo;
}

void main() {
  vec2 px = floor(vUV * uView);
  vec3 raw = texture(uScene, (px + 0.5) / uView).rgb;
  vec3 c = grade(raw, uTod);

  // light shafts slanting through the canopy
  vec2 wp = px + uCam;
  float shaft = vnoise(vec2((wp.x + wp.y * 0.55) * 0.012 + uTime * 0.05, 0.0));
  shaft = smoothstep(0.62, 0.95, shaft) * (0.5 + 0.5 * sin(uTime * 0.3));
  vec3 shaftCol = uTod > 1.5 && uTod < 2.5 ? vec3(1.0, 0.7, 0.4) : vec3(1.0, 0.92, 0.7);
  if (uTod < 2.5) c += shaftCol * shaft * 0.09;

  // low drifting mist
  float mist = fbm(wp * 0.004 + vec2(uTime * 0.02, uTime * 0.008));
  c = mix(c, vec3(0.75, 0.68, 0.58) * (uTod > 2.5 ? 0.35 : 1.0), smoothstep(0.55, 0.85, mist) * 0.18);

  // night: lanterns and fireflies light the dark
  if (uTod > 2.5) {
    vec3 add = vec3(0.0);
    for (int i = 0; i < 16; i++) {
      if (i >= uLightCount) break;
      vec4 L = uLights[i];
      float d = length(px - L.xy) / L.z;
      add += vec3(1.0, 0.72, 0.38) * L.w * pow(max(0.0, 1.0 - d), 2.0);
    }
    // lit areas fall back toward the day colour, warmed: firelight, not a torch beam
    vec3 warm = grade(raw, 1.0) * vec3(1.18, 0.86, 0.58);
    c = mix(c, warm, clamp(add.r * 1.25, 0.0, 1.0)) + add * 0.04;
  }

  // vignette
  vec2 q = vUV - 0.5;
  c *= 1.0 - dot(q, q) * 0.9;
  // ordered dither before quantising, keeps gradients pixel-art friendly
  int x = int(mod(px.x, 4.0)), y = int(mod(px.y, 4.0));
  int m[16] = int[16](0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5);
  float d = (float(m[x + y * 4]) / 16.0 - 0.5) / 64.0;
  c = floor((c + d) * 64.0 + 0.5) / 64.0;
  outColor = vec4(c, 1.0);
}
`;
