#!/usr/bin/env node

// Canonical, dependency-free marketplace validator. The same semantic contract is exported
// for fixture tests; the CLI is only a Controller that reports the result and its exit code.

import { existsSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { basename, dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import {
  BUNDLE_EXTENSIONS, BUNDLE_FILENAME, LEGACY_INDEX_FIELDS, MAX_BUNDLE_FILE_BYTES,
  MAX_BUNDLE_FILES, MAX_BUNDLE_TOTAL_BYTES, SCHEMA_VERSIONS, SEMVER_PATTERN,
  SHA256_PATTERN, SOURCE_REPO, bundleInventory, classifyBundlePath,
  dependencyIdAndConstraint, isImmutableRevision, isSafePluginPath,
  isSafeRelativePath, packageDigest, permissionErrors, repoRelative,
  resolveCompositionGraph, sourceFragment, validateAssetBytes,
} from "./marketplace-contract.mjs";

const MANIFEST = "chainabit-plugin.json";
const SKILL_NAME_PATTERN = /^[a-z0-9]+(-[a-z0-9]+)*$/;
const ID_PATTERN = /^(skill|agent|provider|persona|hook|plugin)-[a-z0-9]+(-[a-z0-9]+)*$/;
const CATEGORIES = new Set([
  "foundations", "web", "languages", "frameworks", "artifacts", "providers", "personas",
  "research", "code", "security", "productivity", "data", "devops", "infrastructure",
  "databases", "cloud", "testing", "ai", "tooling",
]);
const PHYSICAL_CATEGORIES = new Set([
  "foundations", "web", "languages", "frameworks", "artifacts", "providers", "personas",
  "infrastructure", "databases", "cloud", "devops", "testing", "security", "data", "ai", "tooling",
]);

const readJson = (path) => JSON.parse(readFileSync(path, "utf8"));
const fail = (problems, where, message) => problems.push({ where, message });

function frontmatterName(path) {
  let text;
  try { text = readFileSync(path, "utf8"); } catch { return null; }
  const block = /^---\r?\n([\s\S]*?)\r?\n---/.exec(text);
  if (!block) return null;
  return /^name:[ \t]*(\S+)[ \t]*$/m.exec(block[1])?.[1] ?? null;
}

function discoverPluginRoots(root, problems) {
  const roots = [];
  const visit = (current) => {
    for (const entry of readdirSync(current, { withFileTypes: true })) {
      if (entry.isSymbolicLink()) fail(problems, repoRelative(root, join(current, entry.name)), "symlinks are forbidden in distributable plugin content");
    }
    if (existsSync(join(current, MANIFEST))) {
      roots.push({ absolute: current, path: repoRelative(root, current) });
      return;
    }
    for (const entry of readdirSync(current, { withFileTypes: true })) {
      if (entry.isDirectory() && !entry.name.startsWith(".")) visit(join(current, entry.name));
    }
  };
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    if (entry.isDirectory() && PHYSICAL_CATEGORIES.has(entry.name)) visit(join(root, entry.name));
  }
  return roots.sort((a, b) => a.path.localeCompare(b.path));
}

function validateInstallation(manifest, folder, problems) {
  if (manifest.install?.postInstall || manifest.install?.uninstall) fail(problems, folder, "raw postInstall/uninstall commands are forbidden; use installation.packages");
  if (manifest.installation === undefined) return;
  if (!manifest.installation || !Array.isArray(manifest.installation.packages)) { fail(problems, folder, "installation.packages must be an array of structured descriptors"); return; }
  for (const packageSpec of manifest.installation.packages) {
    if (!packageSpec || typeof packageSpec !== "object") { fail(problems, folder, "installation package must be an object"); continue; }
    const { manager, name, version, integrity, scope, executables } = packageSpec;
    if (!["npm", "pip", "cargo", "brew", "system"].includes(manager)) fail(problems, folder, `unsupported package manager ${JSON.stringify(manager)}`);
    if (typeof name !== "string" || !name || /[\s\0]/.test(name)) fail(problems, folder, "installation package name must be a non-empty token");
    if (typeof version !== "string" || !SEMVER_PATTERN.test(version)) fail(problems, folder, `installation package ${name ?? "<unknown>"} must pin an exact semver version`);
    if (typeof integrity !== "string" || !/^(sha256|sha512)-[A-Za-z0-9+/=]+$/.test(integrity)) fail(problems, folder, `installation package ${name ?? "<unknown>"} must declare sha256-/sha512- integrity`);
    if (!["user", "global", "workspace"].includes(scope)) fail(problems, folder, `installation package ${name ?? "<unknown>"} has invalid scope`);
    if (executables !== undefined && (!Array.isArray(executables) || executables.some((value) => typeof value !== "string" || !/^[a-zA-Z0-9._-]+$/.test(value)))) fail(problems, folder, "installation executables must be safe command names");
  }
}

function validateDependenciesAndCapabilities(manifest, folder, problems) {
  if (manifest.dependencies !== undefined) {
    const dependencies = manifest.dependencies;
    if (!dependencies || typeof dependencies !== "object" || Array.isArray(dependencies)) {
      fail(problems, folder, "dependencies must be an object with required and optional maps");
    } else {
      for (const kind of ["required", "optional"]) {
        if (!dependencies[kind] || typeof dependencies[kind] !== "object" || Array.isArray(dependencies[kind])) fail(problems, folder, `dependencies.${kind} must be an object`);
      }
      const required = new Set(Object.keys(dependencies.required ?? {}));
      for (const id of Object.keys(dependencies.optional ?? {})) if (required.has(id)) fail(problems, folder, `dependency ${id} cannot be both required and optional`);
    }
  }
  if (manifest.capabilities !== undefined) {
    const capabilities = manifest.capabilities;
    if (!capabilities || typeof capabilities !== "object" || Array.isArray(capabilities)) {
      fail(problems, folder, "capabilities must be an object with declared and requested arrays");
    } else {
      for (const kind of ["declared", "requested"]) if (!Array.isArray(capabilities[kind])) fail(problems, folder, `capabilities.${kind} must be an array`);
      const ids = new Set();
      for (const capability of capabilities.declared ?? []) {
        if (!capability || typeof capability !== "object" || typeof capability.id !== "string" || !["skill", "plugin", "provider", "validator"].includes(capability.kind) || !Array.isArray(capability.verifiedBehaviors) || capability.verifiedBehaviors.length === 0) fail(problems, folder, "each declared capability needs id, supported kind, and verifiedBehaviors");
        if (capability?.id && ids.has(capability.id)) fail(problems, folder, `duplicate declared capability ${capability.id}`);
        if (capability?.id) ids.add(capability.id);
      }
      for (const requested of capabilities.requested ?? []) if (typeof requested !== "string" || !requested) fail(problems, folder, "capabilities.requested contains an invalid capability id");
    }
  }
}

function validateBundle(pluginRoot, skillPath, folder, manifest, problems, writeBundles, written) {
  const skillRoot = join(pluginRoot, skillPath);
  if (!statSync(skillRoot).isDirectory()) return;
  const document = join(skillRoot, "SKILL.md");
  if (!existsSync(document)) { fail(problems, folder, `skill directory "${skillPath}" has no SKILL.md`); return; }
  const declaredName = frontmatterName(document);
  const directoryName = basename(skillPath);
  if (!declaredName) fail(problems, folder, `${skillPath}/SKILL.md has no name in YAML frontmatter`);
  else if (!SKILL_NAME_PATTERN.test(declaredName) || declaredName !== directoryName) fail(problems, folder, `${skillPath}/SKILL.md name must match its lowercase directory name`);

  let computed;
  try { computed = bundleInventory(skillRoot); } catch (error) { fail(problems, folder, error.message); return; }
  if (computed.files.length === 0 || computed.files.length > MAX_BUNDLE_FILES) fail(problems, folder, `${skillPath}/${BUNDLE_FILENAME} must describe 1-${MAX_BUNDLE_FILES} files`);
  let total = 0;
  for (const file of computed.files) {
    const ext = file.path.slice(file.path.lastIndexOf(".")).toLowerCase();
    if (!BUNDLE_EXTENSIONS.has(ext)) fail(problems, folder, `${skillPath}/${file.path} uses an unsupported bundle extension`);
    if (file.bytes > MAX_BUNDLE_FILE_BYTES) fail(problems, folder, `${skillPath}/${file.path} exceeds the per-file limit`);
    total += file.bytes;
    if (file.type === "assets") {
      const error = validateAssetBytes(file.path, readFileSync(join(skillRoot, ...file.path.split("/"))));
      if (error) fail(problems, folder, `${skillPath}/${file.path}: ${error}`);
    }
  }
  if (computed.files.some((file) => file.type === "scripts") && manifest.permissions?.execute !== true) fail(problems, folder, `${skillPath} contains scripts and requires permissions.execute=true`);
  if (total > MAX_BUNDLE_TOTAL_BYTES) fail(problems, folder, `${skillPath} exceeds the aggregate bundle limit`);
  const bundlePath = join(skillRoot, BUNDLE_FILENAME);
  if (writeBundles) { writeFileSync(bundlePath, `${JSON.stringify(computed, null, 2)}\n`, "utf8"); written.push(`${folder}/${skillPath}/${BUNDLE_FILENAME}`); }
  if (!existsSync(bundlePath)) { fail(problems, folder, `${skillPath} has no ${BUNDLE_FILENAME}`); return; }
  let declared;
  try { declared = readJson(bundlePath); } catch (error) { fail(problems, folder, `${skillPath}/${BUNDLE_FILENAME} is not parseable: ${error.message}`); return; }
  if (!Array.isArray(declared.files)) { fail(problems, folder, `${skillPath}/${BUNDLE_FILENAME} has no files[]`); return; }
  const actualByPath = new Map(computed.files.map((file) => [file.path, file]));
  const declaredByPath = new Map();
  for (const file of declared.files) {
    if (!file || typeof file !== "object" || !isSafeRelativePath(file.path) || declaredByPath.has(file.path) || !SHA256_PATTERN.test(file.sha256 ?? "") || !Number.isInteger(file.bytes) || !["instructions", "references", "scripts", "assets", "metadata"].includes(file.type)) { fail(problems, folder, `${skillPath}/${BUNDLE_FILENAME} contains an invalid file entry`); continue; }
    declaredByPath.set(file.path, file);
  }
  for (const path of declaredByPath.keys()) if (!actualByPath.has(path)) fail(problems, folder, `${skillPath}/${BUNDLE_FILENAME} declares missing file ${path}`);
  for (const [path, actual] of actualByPath) {
    const expected = declaredByPath.get(path);
    if (!expected) { fail(problems, folder, `${skillPath}/${path} is not declared in ${BUNDLE_FILENAME}`); continue; }
    if (expected.sha256 !== actual.sha256 || expected.bytes !== actual.bytes || expected.type !== classifyBundlePath(path)) fail(problems, folder, `${skillPath}/${BUNDLE_FILENAME} is stale or misclassified for ${path}`);
  }
}

export function validateMarketplace(root, { writeBundles = false } = {}) {
  const problems = [];
  const written = [];
  const pluginRoots = discoverPluginRoots(root, problems);
  const manifests = new Map();
  const packageDigests = new Map();
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    if (entry.isDirectory() && !entry.name.startsWith(".") && !PHYSICAL_CATEGORIES.has(entry.name) && entry.name !== "tooling" && existsSync(join(root, entry.name, MANIFEST))) fail(problems, entry.name, "plugin roots must live under a canonical physical category");
  }

  for (const plugin of pluginRoots) {
    const folder = plugin.path;
    let manifest;
    try { manifest = readJson(join(plugin.absolute, MANIFEST)); } catch (error) { fail(problems, folder, `${MANIFEST} is not parseable: ${error.message}`); continue; }
    if (typeof manifest.id === "string" && manifests.has(manifest.id)) fail(problems, folder, `duplicate plugin id "${manifest.id}"`);
    if (typeof manifest.id === "string") manifests.set(manifest.id, { manifest, ...plugin });
    if (!SCHEMA_VERSIONS.has(manifest.schemaVersion)) fail(problems, folder, `schemaVersion must be one of ${[...SCHEMA_VERSIONS].join(", ")}`);
    if (!ID_PATTERN.test(manifest.id ?? "")) fail(problems, folder, "id must match <kind>-<slug>");
    if (typeof manifest.name !== "string" || manifest.name.length < 1 || manifest.name.length > 64) fail(problems, folder, "name must be 1-64 characters");
    if (!SEMVER_PATTERN.test(manifest.version ?? "")) fail(problems, folder, `version ${JSON.stringify(manifest.version)} is not semver`);
    if (!Array.isArray(manifest.categories)) fail(problems, folder, "categories must be an array");
    for (const category of manifest.categories ?? []) if (!CATEGORIES.has(category)) fail(problems, folder, `unknown category ${JSON.stringify(category)}`);
    if (manifest.signature !== undefined) fail(problems, folder, "signature is deprecated; use an externally owned signed attestation");
    for (const error of permissionErrors(manifest)) fail(problems, folder, error);
    validateInstallation(manifest, folder, problems);
    validateDependenciesAndCapabilities(manifest, folder, problems);
    const components = manifest.components ?? {};
    for (const kind of ["agents", "commands"]) {
      if (components[kind] !== undefined && !Array.isArray(components[kind])) fail(problems, folder, `components.${kind} must be an array`);
      for (const component of components[kind] ?? []) if (!isSafeRelativePath(component) || !existsSync(join(plugin.absolute, component)) || !statSync(join(plugin.absolute, component)).isFile()) fail(problems, folder, `invalid or missing ${kind} component ${JSON.stringify(component)}`);
    }
    const skills = Array.isArray(components.skills) ? components.skills : [];
    if (components.skills !== undefined && !Array.isArray(components.skills)) fail(problems, folder, "components.skills must be an array");
    for (const skill of skills) {
      if (!isSafeRelativePath(skill)) { fail(problems, folder, `invalid skill path ${JSON.stringify(skill)}`); continue; }
      const absolute = join(plugin.absolute, skill);
      if (skill.endsWith("SKILL.md")) {
        if (!existsSync(absolute) || !statSync(absolute).isFile()) fail(problems, folder, `missing skill ${skill}`);
      } else {
        if (manifest.schemaVersion < 2) fail(problems, folder, `skill directory ${skill} requires schemaVersion 2`);
        if (!existsSync(absolute) || !statSync(absolute).isDirectory()) fail(problems, folder, `missing skill directory ${skill}`);
        else validateBundle(plugin.absolute, skill, folder, manifest, problems, writeBundles, written);
      }
    }
    for (const validator of manifest.validators ?? []) if (!validator || !isSafeRelativePath(validator.entrypoint) || !existsSync(join(plugin.absolute, validator.entrypoint))) fail(problems, folder, "validator entrypoint must name an existing safe file");
    for (const diagnostic of manifest.diagnostics ?? []) if (!diagnostic || !isSafeRelativePath(diagnostic.entrypoint) || !existsSync(join(plugin.absolute, diagnostic.entrypoint))) fail(problems, folder, "diagnostic entrypoint must name an existing safe file");
    for (const generator of manifest.artifactContract?.generators ?? []) {
      if (
        !generator ||
        !isSafeRelativePath(generator.entrypoint) ||
        !existsSync(join(plugin.absolute, generator.entrypoint)) ||
        !statSync(join(plugin.absolute, generator.entrypoint)).isFile()
      ) {
        fail(problems, folder, "artifact generator entrypoint must name an existing safe file");
      }
    }
    const requires = manifest.composition?.requires ?? [];
    if (!Array.isArray(requires)) fail(problems, folder, "composition.requires must be an array");
    if (manifest.composition?.role === "compatibility" && (!manifest.composition.aliasOf || (components.skills ?? []).length || (components.agents ?? []).length)) fail(problems, folder, "compatibility plugins must declare aliasOf and no duplicate payload");
    try { packageDigests.set(manifest.id, packageDigest(plugin.absolute)); } catch (error) { fail(problems, folder, error.message); }
  }

  for (const [id, entry] of manifests) {
    for (const raw of entry.manifest.composition?.requires ?? []) {
      const dependency = dependencyIdAndConstraint(raw);
      if (!dependency.id || !ID_PATTERN.test(dependency.id) || !manifests.has(dependency.id)) fail(problems, entry.path, `composition dependency ${JSON.stringify(raw)} is missing or invalid`);
      if (dependency.constraint !== null && typeof dependency.constraint !== "string") fail(problems, entry.path, `composition dependency ${JSON.stringify(raw)} has an invalid version constraint`);
    }
    try { resolveCompositionGraph(manifests, [id]); } catch (error) { fail(problems, entry.path, error.message); }
  }

  let listing;
  try { listing = readJson(join(root, "marketplace.json")); } catch (error) { fail(problems, "marketplace.json", `not parseable: ${error.message}`); }
  if (listing) {
    if (!Number.isInteger(listing.schemaVersion) || listing.schemaVersion < 2) fail(problems, "marketplace.json", "schemaVersion 2 is required");
    if (!Array.isArray(listing.plugins)) fail(problems, "marketplace.json", "plugins[] is required");
    const listed = new Map();
    for (const entry of listing.plugins ?? []) {
      if (!entry || typeof entry !== "object") { fail(problems, "marketplace.json", "listing entry must be an object"); continue; }
      if (listed.has(entry.id)) fail(problems, "marketplace.json", `duplicate listing id ${JSON.stringify(entry.id)}`);
      listed.set(entry.id, entry);
      for (const field of LEGACY_INDEX_FIELDS) if (field in entry) fail(problems, "marketplace.json", `${field} is registry-owned and forbidden in authored marketplace metadata`);
      const fragment = sourceFragment(entry.source);
      if (!fragment || !isImmutableRevision(entry.revision)) fail(problems, "marketplace.json", `${entry.id} must use ${SOURCE_REPO} and a 40-character immutable revision`);
      if (!entry.integrity || entry.integrity.algorithm !== "sha256" || !SHA256_PATTERN.test(entry.integrity.packageSha256 ?? "")) fail(problems, "marketplace.json", `${entry.id} must declare integrity.packageSha256`);
      const found = manifests.get(entry.id);
      if (!found) fail(problems, "marketplace.json", `lists ${JSON.stringify(entry.id)} without a matching plugin folder`);
      else {
        if (fragment !== found.path) fail(problems, "marketplace.json", `${entry.id} source fragment does not resolve to ${found.path}`);
        if (entry.version !== found.manifest.version) fail(problems, "marketplace.json", `${entry.id} version drifts from its manifest`);
        if (entry.integrity?.packageSha256 !== packageDigests.get(entry.id)) fail(problems, "marketplace.json", `${entry.id} package digest does not match its content`);
      }
    }
    for (const [id, entry] of manifests) if (!listed.has(id)) fail(problems, "marketplace.json", `${id} exists at ${entry.path} but is unlisted`);
  }
  return { problems, written, pluginFolders: pluginRoots.map((entry) => entry.path), packageDigests, manifests };
}

function main() {
  const root = join(dirname(fileURLToPath(import.meta.url)), "..");
  const result = validateMarketplace(root, { writeBundles: process.argv.includes("--write-bundles") });
  for (const path of result.written) console.log(`  WROTE ${path}`);
  console.log(`\nValidated ${result.pluginFolders.length} plugin folder(s) against the canonical contract.\n`);
  if (result.problems.length === 0) { console.log("marketplace.json is consistent with every plugin folder.\n"); return; }
  for (const problem of result.problems) console.log(`  FAIL  ${problem.where}: ${problem.message}`);
  console.log(`\n${result.problems.length} problem(s) found.\n`);
  process.exitCode = 1;
}

if (pathToFileURL(process.argv[1] ?? "").href === import.meta.url) main();
