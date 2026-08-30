#!/usr/bin/env node
// Resolve the smallest methodology context for selected plugin ids.
// This is intentionally metadata-driven: it does not infer authority and it never
// loads every installed skill. Use: node tooling/resolve-skills.mjs skill-django

import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import {
  packageDigest,
  resolveCompositionGraph,
  sourceFragment,
} from "./marketplace-contract.mjs";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const marketplace = JSON.parse(readFileSync(join(root, "marketplace.json"), "utf8"));
const manifests = new Map();
for (const entry of marketplace.plugins) {
  const fragment = sourceFragment(entry.source);
  if (!fragment || !/^[a-f0-9]{40}$/.test(entry.revision ?? "")) throw new Error(`Unpinned marketplace source for ${entry.id}`);
  const pluginRoot = join(root, ...fragment.split("/"));
  const manifest = JSON.parse(readFileSync(join(pluginRoot, "chainabit-plugin.json"), "utf8"));
  if (manifest.id !== entry.id) throw new Error(`Manifest identity mismatch at ${fragment}: ${manifest.id}`);
  if (manifest.version !== entry.version) throw new Error(`Version mismatch for ${entry.id}`);
  if (entry.integrity?.packageSha256 !== packageDigest(pluginRoot)) throw new Error(`Package integrity mismatch for ${entry.id}`);
  manifests.set(entry.id, { manifest, pluginRoot, path: fragment });
}

const requested = process.argv.slice(2).filter((id) => !id.startsWith("-"));
const includeCore = process.argv.includes("--core");
if (requested.length === 0) {
  console.error("Usage: node tooling/resolve-skills.mjs <plugin-id> [...plugin-id]");
  process.exit(2);
}

const resolved = resolveCompositionGraph(manifests, requested);

const summary = [...resolved].map((id) => {
  const { manifest, pluginRoot } = manifests.get(id);
  const skills = manifest.components?.skills ?? [];
  const discovery = skills.map((path) => {
    const skillPath = path.endsWith("SKILL.md") ? join(pluginRoot, path) : join(pluginRoot, path, "SKILL.md");
    const text = readFileSync(skillPath, "utf8");
    const match = /^description:[ \t]*(.+)$/m.exec(text);
    return { path, description: match?.[1]?.trim() ?? "" };
  });
  const coreBytes = discovery.reduce((total, item) => total + Buffer.byteLength(item.description), 0);
  const item = { id, role: manifest.composition?.role ?? "capability", discovery, estimatedDiscoveryTokens: Math.ceil(coreBytes / 4) };
  if (includeCore) {
    item.core = discovery.map(({ path }) => {
      const skillPath = path.endsWith("SKILL.md") ? join(pluginRoot, path) : join(pluginRoot, path, "SKILL.md");
      return { path, content: readFileSync(skillPath, "utf8") };
    });
  }
  return item;
});
console.log(JSON.stringify({
  requested,
  resolved,
  discovery: summary,
  estimatedDiscoveryTokens: summary.reduce((total, item) => total + item.estimatedDiscoveryTokens, 0),
  note: "Estimate is UTF-8 bytes / 4; core instructions and references are loaded only after selection.",
}, null, 2));
