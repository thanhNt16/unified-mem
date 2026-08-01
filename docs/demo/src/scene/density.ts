// CBM visual-density compensation (MIT, DeusData). Edge scale keeps glow flat
// as edge count grows; node boost + bloom fade gently once discrete point
// sprites overlap at very large cloud sizes.

export const EDGE_REFERENCE_COUNT = 2500;
const EDGE_MIN_SCALE = 0.05;

export function edgeIntensityScale(edgeCount: number): number {
  if (edgeCount <= EDGE_REFERENCE_COUNT) return 1;
  return Math.max(EDGE_MIN_SCALE, Math.sqrt(EDGE_REFERENCE_COUNT / edgeCount));
}

export const NODE_REFERENCE_COUNT = 25000;
const NODE_FADE_END = 250000;
const BLOOM_FLOOR = 0.7;
const NODE_BOOST_FLOOR = 0.8;

function fadeFactor(nodeCount: number): number {
  if (nodeCount <= NODE_REFERENCE_COUNT) return 0;
  return Math.min(1, (nodeCount - NODE_REFERENCE_COUNT) / (NODE_FADE_END - NODE_REFERENCE_COUNT));
}

export function bloomIntensityScale(nodeCount: number): number {
  return 1 - fadeFactor(nodeCount) * (1 - BLOOM_FLOOR);
}

export function nodeBoostScale(nodeCount: number): number {
  return 1 - fadeFactor(nodeCount) * (1 - NODE_BOOST_FLOOR);
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
