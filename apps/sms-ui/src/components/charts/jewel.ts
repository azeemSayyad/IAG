// Jewel palette — ported verbatim from the Gamified dashboard
// (src/components/portal-v2/mockData.ts). The light/mid/deep stops per gem are
// what give the canvas 3D charts their true gradient shading + glow.
export const JEWEL_PALETTE = {
  sapphire: { light: "#93C5FD", mid: "#3B82F6", deep: "#1E40AF", solid: "#1E40AF", glow: "rgba(59, 130, 246, 0.35)" },
  topaz: { light: "#FDE68A", mid: "#F59E0B", deep: "#B45309", solid: "#B8893A", glow: "rgba(245, 158, 11, 0.40)" },
  emerald: { light: "#A7F3D0", mid: "#10B981", deep: "#047857", solid: "#047857", glow: "rgba(16, 185, 129, 0.35)" },
  rose: { light: "#FECACA", mid: "#F43F5E", deep: "#BE123C", solid: "#BE123C", glow: "rgba(244, 63, 94, 0.32)" },
  onyx: { light: "#94A3B8", mid: "#475569", deep: "#1E293B", solid: "#334155", glow: "rgba(71, 85, 105, 0.40)" },
  // Extended gems so the "Deals by agent" donut can give EVERY agent a distinct,
  // properly-shaded colour (not just the top 5). The first five stay sapphire→onyx
  // so the other charts (TopPerformers3D / WeeklyBars3D, which reference gems by
  // name) and existing colour positions are unchanged.
  amethyst: { light: "#C4B5FD", mid: "#8B5CF6", deep: "#6D28D9", solid: "#6D28D9", glow: "rgba(139, 92, 246, 0.35)" },
  aqua: { light: "#A5F3FC", mid: "#06B6D4", deep: "#0E7490", solid: "#0E7490", glow: "rgba(6, 182, 212, 0.35)" },
  tangerine: { light: "#FED7AA", mid: "#F97316", deep: "#C2410C", solid: "#C2410C", glow: "rgba(249, 115, 22, 0.38)" },
  peridot: { light: "#D9F99D", mid: "#84CC16", deep: "#4D7C0F", solid: "#4D7C0F", glow: "rgba(132, 204, 22, 0.35)" },
  magenta: { light: "#F5D0FE", mid: "#D946EF", deep: "#A21CAF", solid: "#A21CAF", glow: "rgba(217, 70, 239, 0.34)" },
  indigo: { light: "#A5B4FC", mid: "#6366F1", deep: "#4338CA", solid: "#4338CA", glow: "rgba(99, 102, 241, 0.35)" },
} as const;

// Flat list of "mid" stops — simple color array for per-series assignment.
// Derived from JEWEL_PALETTE (insertion order) so it auto-extends with the gems.
export const JEWEL_MID: string[] = Object.values(JEWEL_PALETTE).map((g) => g.mid);

type Stops = { light: string; mid: string; deep: string; glow: string };
const STOPS_BY_MID: Record<string, Stops> = Object.fromEntries(
  Object.values(JEWEL_PALETTE).map((g) => [g.mid, { light: g.light, mid: g.mid, deep: g.deep, glow: g.glow }]),
);

// --- colour maths so any generated hue still gets the canvas donut's 3D
// gradient + glow (curated gems keep their hand-tuned stops). ---
function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  const n = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  return [parseInt(n.slice(0, 2), 16), parseInt(n.slice(2, 4), 16), parseInt(n.slice(4, 6), 16)];
}
function clampHex(v: number): string {
  return Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0");
}
function mixToward(rgb: [number, number, number], t: [number, number, number], amt: number): string {
  return "#" + (rgb.map((v, i) => clampHex(v + (t[i] - v) * amt)).join(""));
}
function hslToHex(h: number, s: number, l: number): string {
  const sn = s / 100, ln = l / 100;
  const k = (n: number) => (n + h / 30) % 12;
  const a = sn * Math.min(ln, 1 - ln);
  const f = (n: number) => ln - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
  return "#" + clampHex(f(0) * 255) + clampHex(f(8) * 255) + clampHex(f(4) * 255);
}

function deriveStops(c: string): Stops {
  const rgb = hexToRgb(c);
  return {
    light: mixToward(rgb, [255, 255, 255], 0.5),
    mid: c,
    deep: mixToward(rgb, [0, 0, 0], 0.42),
    glow: `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, 0.35)`,
  };
}

export function stopsFor(c: string): Stops {
  return STOPS_BY_MID[c] ?? deriveStops(c);
}

// Distinct colour per agent index. Curated gems first (so the common case keeps
// the premium hand-tuned look); past that, evenly-spread golden-angle jewel-toned
// hues so ANY number of agents stays visually distinguishable. stopsFor() shades
// the generated hues the same way as the gems.
export function agentColor(i: number): string {
  if (i < JEWEL_MID.length) return JEWEL_MID[i];
  const hue = (210 + (i - JEWEL_MID.length + 1) * 137.508) % 360;
  return hslToHex(hue, 70, 56);
}

// roundRect helper (some older canvas impls lack it).
type RR = CanvasRenderingContext2D & {
  roundRect?: (x: number, y: number, w: number, h: number, r: number | number[]) => void;
};
export function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number | number[]) {
  const c = ctx as RR;
  if (c.roundRect) c.roundRect(x, y, w, h, r);
  else ctx.rect(x, y, w, h);
}
