#!/usr/bin/env node

// Release controller: bind the authored index to the exact commit that contains its
// manifests and bundle payloads. It intentionally requires the caller to provide the
// commit identity after committing content, so an index cannot quietly point at a branch.

import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { validateMarketplace } from "./validate-marketplace.mjs";
import { COMMIT_PATTERN } from "./marketplace-contract.mjs";

export function updateMarketplaceRevision(root, revision) {
  if (!COMMIT_PATTERN.test(revision)) throw new Error("revision must be a 40-character lowercase Git commit SHA");
  const validation = validateMarketplace(root);
  if (validation.problems.length) throw new Error(validation.problems.map(({ where, message }) => `${where}: ${message}`).join("\n"));
  const path = join(root, "marketplace.json");
  const index = JSON.parse(readFileSync(path, "utf8"));
  index.plugins = index.plugins.map((entry) => ({
    ...entry,
    revision,
    integrity: { algorithm: "sha256", packageSha256: validation.packageDigests.get(entry.id) },
  }));
  writeFileSync(path, `${JSON.stringify(index, null, 2)}\n`, "utf8");
}

function main() {
  const root = join(dirname(fileURLToPath(import.meta.url)), "..");
  const revision = process.argv[2];
  if (!revision) throw new Error("usage: node tooling/update-marketplace.mjs <content-commit-sha>");
  updateMarketplaceRevision(root, revision);
  console.log(`Updated ${root}/marketplace.json to revision ${revision}.`);
}

if (pathToFileURL(process.argv[1] ?? "").href === import.meta.url) main();
