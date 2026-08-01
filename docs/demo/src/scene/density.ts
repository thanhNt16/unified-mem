// CBM visual-density compensation (MIT, DeusData). Trimmed: our demo is ≤2k
// nodes so the large-cloud fade paths are gone; adaptive edge scale stays.

export const EDGE_REFERENCE_COUNT = 2500;
const EDGE_MIN_SCALE = 0.05;

export function edgeIntensityScale(edgeCount: number): number {
  if (edgeCount <= EDGE_REFERENCE_COUNT) return 1;
  return Math.max(EDGE_MIN_SCALE, Math.sqrt(EDGE_REFERENCE_COUNT / edgeCount));
}

// Colour-aware glow: blue hubs brightest, red leaves modest, white least.
const GLOW_BASE = 1.35;
const GLOW_BLUE_GAIN = 2.4;
const GLOW_RED_GAIN = 0.9;

export function nodeGlowBoost(r: number, g: number, b: number): number {
  const blueness = Math.max(0, b - Math.max(r, g));
  const redness = Math.max(0, r - Math.max(g, b));
  return GLOW_BASE + blueness * GLOW_BLUE_GAIN + redness * GLOW_RED_GAIN;
}
