#!/usr/bin/env node
// Resolve the smallest methodology context for selected plugin ids.
// This is intentionally metadata-driven: it does not infer authority and it never
// loads every installed skill. Use: node tooling/resolve-skills.mjs skill-django

import { readFileSync } from "node:fs";
import { join, dirname, posix } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const marketplace = JSON.parse(readFileSync(join(root, "marketplace.json"), "utf8"));
const manifests = new Map();
for (const entry of marketplace.plugins) {
  const fragment = entry.source?.split("#")[1];
  if (!fragment || posix.isAbsolute(fragment) || fragment.includes("\\") || fragment.split("/").some((part) => !/^[a-z0-9][a-z0-9._-]{0,63}$/.test(part) || part.startsWith(".") || part.includes(".."))) {
    throw new Error(`Unsafe marketplace path for ${entry.id}: ${fragment ?? "<none>"}`);
  }
  const pluginRoot = join(root, ...fragment.split("/"));
  const manifest = JSON.parse(readFileSync(join(pluginRoot, "chainabit-plugin.json"), "utf8"));
  if (manifest.id !== entry.id) throw new Error(`Manifest identity mismatch at ${fragment}: ${manifest.id}`);
  manifests.set(entry.id, { manifest, pluginRoot, path: fragment });
}

const requested = process.argv.slice(2).filter((id) => !id.startsWith("-"));
const includeCore = process.argv.includes("--core");
if (requested.length === 0) {
  console.error("Usage: node tooling/resolve-skills.mjs <plugin-id> [...plugin-id]");
  process.exit(2);
}

const resolved = [];
const states = new Map();
const visit = (id, trail = []) => {
  if (states.get(id) === "done") return;
  if (states.get(id) === "visiting") throw new Error(`Dependency cycle: ${[...trail, id].join(" -> ")}`);
  const entry = manifests.get(id);
  if (!entry) throw new Error(`Unknown plugin: ${id}`);
  states.set(id, "visiting");
  for (const dependency of entry.manifest.composition?.requires ?? []) visit(dependency, [...trail, id]);
  states.set(id, "done");
  resolved.push(id);
};
for (const id of requested) visit(id);

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
