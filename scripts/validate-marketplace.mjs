// Reference marketplace validator — dependency-free, run with plain `node`.
//
//   node scripts/validate-marketplace.mjs
//
// Checks that this repo is installable as published. A marketplace is only as good as its
// weakest listing: a client resolves an id from `marketplace.json`, clones this repo, reads
// the manifest at the listing's `#<subdirectory>` fragment, and installs what it finds. Any
// break in that chain surfaces to a user as a failed install, so all of it is checked here:
//
//   1. Every plugin folder has a manifest that satisfies the frozen contract.
//   2. Every manifest's declared component files actually exist in its folder.
//   3. `marketplace.json` and the folders on disk describe the same plugins at the same
//      versions, and every listing's `#fragment` names its own folder.
//
// Deliberately implements the contract directly rather than validating against the JSON
// Schema. The schema cannot express the rule that broke this repo before: a manifest with an
// executing component (hooks / install scripts / MCP servers) MUST declare `permissions.shell`.
// That rule lives in the installing client's parser, so a schema-only check passes manifests
// the client then refuses. Keep this file in step with the parser, not just the schema.

import { readFileSync, readdirSync, existsSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const MANIFEST = "chainabit-plugin.json";
const SOURCE_REPO = "https://github.com/chainabit/plugins.git";

const SCHEMA_VERSION = 1;
const ID_PATTERN = /^(skill|agent|provider|persona|hook|plugin)-[a-z0-9]+(-[a-z0-9]+)*$/;
const SEMVER =
  /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z-.]+)?(?:\+[0-9A-Za-z-.]+)?$/;
const CATEGORIES = new Set([
  "research", "code", "security", "productivity",
  "data", "devops", "providers", "personas",
]);

const problems = [];
const fail = (where, message) => problems.push({ where, message });

const readJson = (path) => JSON.parse(readFileSync(path, "utf8"));

const isDir = (path) => existsSync(path) && statSync(path).isDirectory();

/** Top-level plugin folders: any directory holding a manifest. */
const pluginFolders = readdirSync(repoRoot)
  .filter((name) => !name.startsWith(".") && name !== "scripts")
  .filter((name) => isDir(join(repoRoot, name)))
  .sort();

// --- 1 + 2: every folder's manifest satisfies the contract -----------------------

const manifests = new Map();

for (const folder of pluginFolders) {
  const manifestPath = join(repoRoot, folder, MANIFEST);
  if (!existsSync(manifestPath)) {
    fail(folder, `no ${MANIFEST} — a client that clones this folder cannot install it`);
    continue;
  }

  let m;
  try {
    m = readJson(manifestPath);
  } catch (e) {
    fail(folder, `${MANIFEST} is not parseable: ${e.message}`);
    continue;
  }
  manifests.set(folder, m);

  if (m.schemaVersion !== SCHEMA_VERSION) {
    fail(folder, `schemaVersion must be ${SCHEMA_VERSION}, found ${m.schemaVersion}`);
  }
  if (!ID_PATTERN.test(m.id ?? "")) {
    fail(folder, `id "${m.id}" is not "<kind>-<slug>"`);
  }
  // The id is how a listing addresses this folder; they must be the same string.
  if (m.id !== folder) {
    fail(folder, `id "${m.id}" does not match its folder name`);
  }
  if (!m.name || m.name.length > 64) {
    fail(folder, `name must be 1–64 characters`);
  }
  if (!SEMVER.test(m.version ?? "")) {
    fail(folder, `version "${m.version}" is not semver`);
  }
  for (const category of m.categories ?? []) {
    if (!CATEGORIES.has(category)) {
      fail(folder, `unknown category "${category}"`);
    }
  }

  const components = m.components ?? {};
  const permissions = m.permissions ?? {};

  // The rule the JSON Schema cannot express — and the one this repo previously broke.
  const executes =
    (components.hooks ?? []).length > 0 ||
    (components.mcpServers ?? []).length > 0 ||
    m.install?.postInstall != null ||
    m.install?.uninstall != null;
  if (executes && permissions.shell !== true) {
    fail(
      folder,
      "declares an executing component (hooks/mcpServers/install) but not " +
        "permissions.shell — the client refuses to install this",
    );
  }

  // Declared component files must exist and stay inside the plugin folder.
  const fileComponents = [
    ...(components.skills ?? []),
    ...(components.agents ?? []),
    ...(components.commands ?? []),
  ];
  for (const relative of fileComponents) {
    if (relative.includes("..") || relative.startsWith("/") || relative.startsWith("~")) {
      fail(folder, `component path "${relative}" escapes the plugin folder`);
      continue;
    }
    if (!existsSync(join(repoRoot, folder, relative))) {
      fail(folder, `declared component "${relative}" does not exist`);
    }
  }

  if (fileComponents.length === 0 && !executes) {
    fail(folder, "declares no components — it would install but route nothing");
  }
}

// --- 3: the listing and the folders agree ----------------------------------------

let listing;
try {
  listing = readJson(join(repoRoot, "marketplace.json"));
} catch (e) {
  fail("marketplace.json", `not parseable: ${e.message}`);
}

if (listing) {
  const listed = new Map((listing.plugins ?? []).map((p) => [p.id, p]));

  for (const id of listed.keys()) {
    if (!manifests.has(id)) {
      fail("marketplace.json", `lists "${id}" but this repo has no such folder`);
    }
  }
  for (const folder of manifests.keys()) {
    if (!listed.has(folder)) {
      fail("marketplace.json", `"${folder}" exists but is not listed — it is unreachable`);
    }
  }

  for (const [id, entry] of listed) {
    const manifest = manifests.get(id);
    if (manifest && entry.version !== manifest.version) {
      fail(
        "marketplace.json",
        `"${id}" is listed at ${entry.version} but its manifest declares ${manifest.version}`,
      );
    }
    const expected = `${SOURCE_REPO}#${id}`;
    if (entry.source !== expected) {
      fail("marketplace.json", `"${id}" source should be ${expected}, found ${entry.source}`);
    }
  }
}

// --- report ----------------------------------------------------------------------

console.log("");
console.log(`Validated ${pluginFolders.length} plugin folder(s) against the frozen contract.`);
console.log("");

if (problems.length === 0) {
  for (const folder of pluginFolders) {
    const m = manifests.get(folder);
    console.log(`  PASS  ${folder}  ${m ? `v${m.version}` : ""}`);
  }
  console.log("");
  console.log("marketplace.json is consistent with every plugin folder.");
  console.log("");
  process.exit(0);
}

for (const { where, message } of problems) {
  console.log(`  FAIL  ${where}: ${message}`);
}
console.log("");
console.log(`${problems.length} problem(s) found.`);
console.log("");
process.exit(1);
