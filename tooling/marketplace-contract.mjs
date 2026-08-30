import { createHash } from "node:crypto";
import { existsSync, lstatSync, readFileSync, readdirSync } from "node:fs";
import { join, relative, sep } from "node:path";

export const SOURCE_REPO = "https://github.com/chainabit/plugins.git";
export const BUNDLE_FILENAME = "bundle.json";
export const MAX_BUNDLE_FILE_BYTES = 512 * 1024;
export const MAX_BUNDLE_TOTAL_BYTES = 4 * 1024 * 1024;
export const MAX_BUNDLE_FILES = 64;
export const MAX_ASSET_DIMENSION = 8192;
export const SCHEMA_VERSIONS = new Set([1, 2]);
export const SEMVER_PATTERN =
  /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z-.]+)?(?:\+[0-9A-Za-z-.]+)?$/;
export const SHA256_PATTERN = /^[a-f0-9]{64}$/;
export const COMMIT_PATTERN = /^[a-f0-9]{40}$/;

export const BUNDLE_EXTENSIONS = new Set([
  ".md", ".py", ".json", ".txt", ".css", ".csv",
  ".png", ".jpg", ".jpeg", ".webp", ".svg", ".woff", ".woff2",
]);

export const AUTHORITIES = new Set([
  "sandbox.execute",
  "host.process.execute",
  "host.package.install",
  "workspace.read",
  "workspace.write",
  "filesystem.read",
  "filesystem.write",
  "network.connect",
  "environment.read",
  "credential.use",
  "lifecycle.install",
  "lifecycle.uninstall",
]);

export const LEGACY_INDEX_FIELDS = new Set([
  "verified", "downloads", "lastChecked", "stale",
]);

export function repoRelative(root, absolute) {
  return relative(root, absolute).split(sep).join("/");
}

export function isSafeRelativePath(value) {
  if (typeof value !== "string" || value.length === 0 || value.length > 200) return false;
  if (value.startsWith("/") || value.includes("\\") || value.includes("\0")) return false;
  if (value.includes("//") || value.endsWith("/")) return false;
  return value.split("/").every((part) =>
    /^[a-zA-Z0-9_][a-zA-Z0-9._-]{0,63}$/.test(part) &&
    part !== "." && part !== ".." && !part.startsWith("."),
  );
}

export function isSafePluginPath(value) {
  return isSafeRelativePath(value) && value.split("/").length >= 2;
}

export function isSemver(value) {
  return typeof value === "string" && SEMVER_PATTERN.test(value);
}

export function isImmutableRevision(value) {
  return typeof value === "string" && COMMIT_PATTERN.test(value);
}

export function classifyBundlePath(path) {
  if (path === "SKILL.md") return "instructions";
  if (path.startsWith("scripts/")) return "scripts";
  if (path.startsWith("references/")) return "references";
  if (path.startsWith("assets/")) return "assets";
  return "metadata";
}

function extension(path) {
  const dot = path.lastIndexOf(".");
  const slash = path.lastIndexOf("/");
  return dot > slash ? path.slice(dot).toLowerCase() : "";
}

export function validateAssetBytes(path, bytes) {
  const ext = extension(path);
  if (![...BUNDLE_EXTENSIONS].includes(ext)) return "unsupported asset extension";
  if (bytes.byteLength > MAX_BUNDLE_FILE_BYTES) return "asset exceeds per-file limit";
  if (ext === ".png") {
    if (!bytes.subarray(0, 8).equals(Buffer.from("89504e470d0a1a0a", "hex"))) return "invalid PNG signature";
    if (bytes.length >= 24 && (bytes.readUInt32BE(16) > MAX_ASSET_DIMENSION || bytes.readUInt32BE(20) > MAX_ASSET_DIMENSION)) return "PNG dimensions exceed the asset limit";
  }
  if ([".jpg", ".jpeg"].includes(ext) && !(bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[bytes.length - 2] === 0xff && bytes[bytes.length - 1] === 0xd9)) return "invalid JPEG signature";
  if (ext === ".webp" && (bytes.toString("ascii", 0, 4) !== "RIFF" || bytes.toString("ascii", 8, 12) !== "WEBP")) return "invalid WebP signature";
  if (ext === ".woff" && bytes.toString("ascii", 0, 4) !== "wOFF") return "invalid WOFF signature";
  if (ext === ".woff2" && bytes.toString("ascii", 0, 4) !== "wOF2") return "invalid WOFF2 signature";
  if (ext === ".svg") {
    const text = bytes.toString("utf8").toLowerCase();
    if (!text.includes("<svg") || /<\s*script\b|\bon[a-z]+\s*=|javascript:|(?:href|src)\s*=\s*["'](?:https?:|\/\/)/i.test(text)) return "SVG contains executable or remote content";
  }
  if (ext === ".css" && /@import|url\s*\(\s*["']?(?:https?:|\/\/)/i.test(bytes.toString("utf8"))) return "CSS contains remote content";
  return null;
}

function listFiles(root, current = root) {
  const files = [];
  for (const entry of readdirSync(current, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
    const absolute = join(current, entry.name);
    if (entry.isSymbolicLink()) throw new Error(`symlink is not allowed: ${repoRelative(root, absolute)}`);
    if (entry.isDirectory()) files.push(...listFiles(root, absolute));
    else if (entry.isFile()) files.push(absolute);
    else throw new Error(`unsupported filesystem entry: ${repoRelative(root, absolute)}`);
  }
  return files;
}

export function packageDigest(pluginRoot) {
  const lines = [];
  for (const absolute of listFiles(pluginRoot)) {
    const path = repoRelative(pluginRoot, absolute);
    if (path === BUNDLE_FILENAME || path.endsWith(`/${BUNDLE_FILENAME}`)) continue;
    const bytes = readFileSync(absolute);
    const digest = createHash("sha256").update(bytes).digest("hex");
    lines.push(`${path}\0${digest}\0${bytes.length}\n`);
  }
  return createHash("sha256").update(lines.sort().join(""), "utf8").digest("hex");
}

export function bundleInventory(skillRoot) {
  const files = listFiles(skillRoot)
    .map((absolute) => repoRelative(skillRoot, absolute))
    .filter((path) => path !== BUNDLE_FILENAME)
    .sort()
    .map((path) => {
      const bytes = readFileSync(join(skillRoot, ...path.split("/")));
      return {
        path,
        type: classifyBundlePath(path),
        sha256: createHash("sha256").update(bytes).digest("hex"),
        bytes: bytes.length,
      };
    });
  return { files };
}

export function expectedAuthorities(manifest) {
  const permissions = manifest.permissions ?? {};
  const components = manifest.components ?? {};
  const expected = new Set();
  if (permissions.execute === true) expected.add("sandbox.execute");
  if ((components.hooks ?? []).length || (components.mcpServers ?? []).length || permissions.shell === true) expected.add("host.process.execute");
  if (manifest.install || manifest.installation) expected.add("lifecycle.install");
  if (manifest.installation?.packages?.length) expected.add("host.package.install");
  if ((permissions.network ?? []).length) expected.add("network.connect");
  if ((permissions.filesystem ?? []).length) expected.add("filesystem.read");
  if (permissions.workspaceAccess === true) expected.add("workspace.read");
  return expected;
}

export function permissionErrors(manifest) {
  const errors = [];
  const permissions = manifest.permissions;
  if (!permissions || !Array.isArray(permissions.requested)) {
    errors.push("permissions.requested must be an explicit array of requested authority names");
    return errors;
  }
  const seen = new Set();
  for (const authority of permissions.requested) {
    if (typeof authority !== "string" || !AUTHORITIES.has(authority)) errors.push(`unknown requested authority ${JSON.stringify(authority)}`);
    if (seen.has(authority)) errors.push(`duplicate requested authority ${JSON.stringify(authority)}`);
    seen.add(authority);
  }
  if (permissions.shell === true) errors.push("permissions.shell is deprecated and must be false; use granular authorities");
  if (permissions.dangerousSkipPermissions === true) errors.push("dangerousSkipPermissions is not allowed in a distributable manifest");
  for (const required of expectedAuthorities(manifest)) {
    if (!seen.has(required)) errors.push(`component requires explicit requested authority ${required}`);
  }
  if (manifest.install?.postInstall || manifest.install?.uninstall) errors.push("raw install commands are forbidden; use installation.packages descriptors");
  return errors;
}

function parseVersion(value) {
  const match = /^(\d+)\.(\d+)\.(\d+)/.exec(value ?? "");
  return match ? match.slice(1).map(Number) : null;
}

export function satisfiesVersion(version, constraint) {
  if (!isSemver(version) || typeof constraint !== "string" || !constraint.trim()) return false;
  const actual = parseVersion(version);
  const raw = constraint.trim();
  if (raw === "*" || raw === "latest") return true;
  if (isSemver(raw)) return version === raw;
  const caret = /^\^(\d+)\.(\d+)\.(\d+)/.exec(raw);
  if (caret) return actual[0] === Number(caret[1]) && (actual[1] > Number(caret[2]) || (actual[1] === Number(caret[2]) && actual[2] >= Number(caret[3])));
  const tilde = /^~(\d+)\.(\d+)\.(\d+)/.exec(raw);
  if (tilde) return actual[0] === Number(tilde[1]) && actual[1] === Number(tilde[2]) && actual[2] >= Number(tilde[3]);
  return false;
}

export function dependencyIdAndConstraint(dependency) {
  if (typeof dependency === "string") return { id: dependency, constraint: null };
  if (dependency && typeof dependency === "object") return { id: dependency.id, constraint: dependency.version ?? dependency.range ?? null };
  return { id: null, constraint: null };
}

export function resolveCompositionGraph(manifests, requested) {
  const result = [];
  const state = new Map();
  const visit = (id, trail = []) => {
    if (state.get(id) === "done") return;
    if (state.get(id) === "visiting") throw new Error(`dependency cycle: ${[...trail, id].join(" -> ")}`);
    const entry = manifests.get(id);
    if (!entry) throw new Error(`missing dependency: ${id}`);
    state.set(id, "visiting");
    for (const raw of entry.manifest.composition?.requires ?? []) {
      const dependency = dependencyIdAndConstraint(raw);
      if (!dependency.id) throw new Error(`invalid dependency in ${id}`);
      const target = manifests.get(dependency.id);
      if (!target) throw new Error(`missing dependency: ${dependency.id}`);
      if (dependency.constraint && !satisfiesVersion(target.manifest.version, dependency.constraint)) throw new Error(`dependency conflict: ${id} requires ${dependency.id}@${dependency.constraint}, found ${target.manifest.version}`);
      visit(dependency.id, [...trail, id]);
    }
    state.set(id, "done");
    result.push(id);
  };
  for (const id of requested) visit(id);
  return result;
}

export function sourceFragment(source) {
  if (typeof source !== "string") return null;
  const [repo, fragment] = source.split("#");
  if (repo !== SOURCE_REPO || !isSafePluginPath(fragment)) return null;
  return fragment;
}

export function assertPackagePath(root) {
  if (!existsSync(root) || !lstatSync(root).isDirectory()) throw new Error(`plugin root is not a directory: ${root}`);
}
