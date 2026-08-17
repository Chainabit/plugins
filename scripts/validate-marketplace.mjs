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
// Schema. The schema cannot express the rules that break an install:
//
//   - A manifest with an executing component (hooks / install scripts / MCP servers) MUST
//     declare `permissions.shell`. That is the rule that broke this repo before.
//   - A schemaVersion 2 skill directory shipping `scripts/` MUST declare
//     `permissions.execute`, so the consent sheet can say what the user is accepting.
//   - A skill directory's basename MUST equal the `name` in its SKILL.md frontmatter,
//     because the two are separate declarations of the same identity and nothing else
//     catches them drifting.
//
// Those rules live in the installing client's parser, so a schema-only check passes manifests
// the client then refuses. Keep this file in step with the parser, not just the schema.

import { readFileSync, readdirSync, existsSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { basename, dirname, join } from "node:path";

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const MANIFEST = "chainabit-plugin.json";
const SOURCE_REPO = "https://github.com/chainabit/plugins.git";

// 1 is the original frozen contract; 2 adds directory-form skills, components.providers,
// and permissions.execute. Both remain installable — 2 is additive.
const SCHEMA_VERSIONS = new Set([1, 2]);
const SKILL_NAME_PATTERN = /^[a-z0-9]+(-[a-z0-9]+)*$/;
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

/** A component path must stay inside its own plugin folder. */
const escapesFolder = (relative) =>
  typeof relative !== "string" ||
  relative.includes("..") ||
  relative.startsWith("/") ||
  relative.startsWith("~");

/**
 * The `name` declared in a SKILL.md's YAML frontmatter, or null when there is no
 * frontmatter block or no name in it. Read with a regex rather than a YAML parser
 * to keep this script dependency-free; the frontmatter contract is one flat block
 * of scalars, so that is enough.
 */
const frontmatterName = (path) => {
  let text;
  try {
    text = readFileSync(path, "utf8");
  } catch {
    return null;
  }
  const block = /^---\r?\n([\s\S]*?)\r?\n---/.exec(text);
  if (!block) return null;
  const name = /^name:[ \t]*(\S+)[ \t]*$/m.exec(block[1]);
  return name ? name[1] : null;
};

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

  if (!SCHEMA_VERSIONS.has(m.schemaVersion)) {
    fail(
      folder,
      `schemaVersion must be one of ${[...SCHEMA_VERSIONS].join(", ")}, found ${m.schemaVersion}`,
    );
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

  // Agents and commands are plain file paths, unchanged since schemaVersion 1.
  const filePaths = [...(components.agents ?? []), ...(components.commands ?? [])];
  for (const relative of filePaths) {
    if (escapesFolder(relative)) {
      fail(folder, `component path "${relative}" escapes the plugin folder`);
      continue;
    }
    if (!existsSync(join(repoRoot, folder, relative))) {
      fail(folder, `declared component "${relative}" does not exist`);
    }
  }

  // Skills take two forms. A path ending in SKILL.md is the schemaVersion 1 form: one
  // prompt-only document. A directory is the schemaVersion 2 form: SKILL.md plus optional
  // scripts/, references/, assets/. Only the second can carry code, so only the second
  // has anything to consent to.
  const skills = components.skills ?? [];
  let shipsScripts = false;

  for (const relative of skills) {
    if (escapesFolder(relative)) {
      fail(folder, `component path "${relative}" escapes the plugin folder`);
      continue;
    }

    const absolute = join(repoRoot, folder, relative);

    if (relative.endsWith("SKILL.md")) {
      if (!existsSync(absolute)) {
        fail(folder, `declared skill "${relative}" does not exist`);
      }
      continue;
    }

    if (m.schemaVersion < 2) {
      fail(
        folder,
        `skill "${relative}" is a directory path, which requires schemaVersion 2 — ` +
          "a version 1 client only understands a path to a SKILL.md file",
      );
    }
    if (!isDir(absolute)) {
      fail(
        folder,
        `declared skill "${relative}" is neither an existing SKILL.md file nor a directory`,
      );
      continue;
    }

    const document = join(absolute, "SKILL.md");
    if (!existsSync(document)) {
      fail(folder, `skill directory "${relative}" has no SKILL.md — there is nothing to load`);
      continue;
    }

    // The directory name and the frontmatter name are two declarations of one identity.
    const directoryName = basename(relative.replace(/\/+$/, ""));
    const declaredName = frontmatterName(document);
    if (declaredName === null) {
      fail(folder, `${relative}/SKILL.md has no "name" in its YAML frontmatter`);
    } else if (!SKILL_NAME_PATTERN.test(declaredName)) {
      fail(
        folder,
        `${relative}/SKILL.md name "${declaredName}" must be lowercase alphanumerics ` +
          "joined by single hyphens",
      );
    } else if (declaredName !== directoryName) {
      fail(
        folder,
        `${relative}/SKILL.md declares name "${declaredName}" but sits in directory ` +
          `"${directoryName}" — the two must match`,
      );
    }

    if (isDir(join(absolute, "scripts"))) {
      shipsScripts = true;
    }
  }

  // The second rule the schema cannot express: shipping runnable scripts is a capability
  // the user consents to at install, so it has to be declared.
  if (permissions.execute !== undefined && typeof permissions.execute !== "boolean") {
    fail(folder, `permissions.execute must be a boolean, found ${typeof permissions.execute}`);
  }
  if (permissions.execute === true && m.schemaVersion < 2) {
    fail(folder, "permissions.execute requires schemaVersion 2");
  }
  if (shipsScripts && permissions.execute !== true) {
    fail(
      folder,
      "ships a skill directory containing scripts/ but does not declare " +
        "permissions.execute — the install consent sheet would not disclose it",
    );
  }

  // Providers are AI backend identifiers, not paths, so there is nothing on disk to check.
  const providers = components.providers ?? [];
  if (providers.length > 0 && m.schemaVersion < 2) {
    fail(folder, "components.providers requires schemaVersion 2");
  }
  for (const provider of providers) {
    if (typeof provider !== "string" || provider.trim() === "") {
      fail(folder, `components.providers entry ${JSON.stringify(provider)} must be a non-empty string`);
    }
  }

  const routes = filePaths.length + skills.length + providers.length;
  if (routes === 0 && !executes) {
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
