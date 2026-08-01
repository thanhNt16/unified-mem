import { readFile } from "node:fs/promises";

const expected = {
  "graph.json": [1774, 1598],
  "graph-stress.json": [10000, 33917],
};

for (const [file, [nodeCount, edgeCount]] of Object.entries(expected)) {
  const graph = JSON.parse(await readFile(new URL(`../${file}`, import.meta.url), "utf8"));
  if (graph.nodes.length !== nodeCount || graph.edges.length !== edgeCount) {
    throw new Error(`${file}: expected ${nodeCount}/${edgeCount}, got ${graph.nodes.length}/${graph.edges.length}`);
  }

  const ids = new Set(graph.nodes.map((node) => node.id));
  if (ids.size !== graph.nodes.length) throw new Error(`${file}: duplicate node id`);
  for (const edge of graph.edges) {
    if (!ids.has(edge.source) || !ids.has(edge.target)) {
      throw new Error(`${file}: dangling edge ${edge.source} → ${edge.target}`);
    }
  }

  const clusterCounts = new Map();
  for (const node of graph.nodes) {
    clusterCounts.set(node.cluster, (clusterCounts.get(node.cluster) ?? 0) + 1);
  }
  for (const cluster of graph.clusters) {
    if (clusterCounts.get(cluster.id) !== cluster.count) {
      throw new Error(`${file}: cluster ${cluster.id} count mismatch`);
    }
    clusterCounts.delete(cluster.id);
  }
  if (clusterCounts.size) throw new Error(`${file}: missing cluster metadata`);
  console.log(`${file}: ${nodeCount} nodes / ${edgeCount} edges PASS`);
}
