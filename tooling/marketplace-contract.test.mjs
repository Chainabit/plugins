import assert from "node:assert/strict";
import { cpSync, existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import {
  dependencyIdAndConstraint,
  packageDigest,
  permissionErrors,
  resolveCompositionGraph,
  validateAssetBytes,
} from "./marketplace-contract.mjs";
import { validateMarketplace } from "./validate-marketplace.mjs";
import { assertLocalCommit } from "./update-marketplace.mjs";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");

function temporaryRepository(change) {
  const copy = mkdtempSync(join(tmpdir(), "chainabit-marketplace-"));
  cpSync(root, copy, { recursive: true });
  try { return change(copy); } finally { rmSync(copy, { recursive: true, force: true }); }
}

function json(path) { return JSON.parse(readFileSync(path, "utf8")); }
function save(path, value) { writeFileSync(path, `${JSON.stringify(value, null, 2)}\n`); }

test("the checked-in marketplace is valid and every source is immutable", () => {
  const result = validateMarketplace(root);
  assert.deepEqual(result.problems, []);
  const index = json(join(root, "marketplace.json"));
  assert.equal(index.plugins.length, result.pluginFolders.length);
  for (const entry of index.plugins) {
    assert.match(entry.revision, /^[a-f0-9]{40}$/);
    assert.doesNotThrow(() => assertLocalCommit(root, entry.revision));
    assert.match(entry.integrity.packageSha256, /^[a-f0-9]{64}$/);
    assert.equal(entry.integrity.packageSha256, result.packageDigests.get(entry.id));
  }
});

test("release publication rejects a syntactically valid but nonexistent commit", () => {
  assert.throws(
    () => assertLocalCommit(root, "0000000000000000000000000000000000000000"),
    /does not identify a local Git commit/,
  );
});

test("skill-website keeps one canonical implementation and an explicit compatibility alias", () => {
  const alias = json(join(root, "artifacts", "skill-website", "chainabit-plugin.json"));
  assert.equal(alias.id, "skill-website");
  assert.equal(alias.composition.role, "compatibility");
  assert.equal(alias.composition.aliasOf, "skill-static-website");
  assert.equal(readFileSync(join(root, "marketplace.json"), "utf8").includes('"id": "skill-website"'), true);
  assert.equal(existsSync(join(root, "skill-website", "chainabit-plugin.json")), false);
});

test("detects listing version drift, duplicate identity, unsafe paths, and forged signatures", () => {
  temporaryRepository((copy) => {
    const indexPath = join(copy, "marketplace.json");
    const index = json(indexPath);
    index.plugins[0].version = "99.0.0";
    save(indexPath, index);
    let result = validateMarketplace(copy);
    assert.ok(result.problems.some((problem) => problem.message.includes("version drifts")));

    const duplicate = join(copy, "artifacts", "duplicate-plugin");
    mkdirSync(duplicate, { recursive: true });
    save(join(duplicate, "chainabit-plugin.json"), json(join(copy, "artifacts", "skill-pdf", "chainabit-plugin.json")));
    result = validateMarketplace(copy);
    assert.ok(result.problems.some((problem) => problem.message.includes("duplicate plugin id")));

    const manifestPath = join(copy, "personas", "persona-reviewer", "chainabit-plugin.json");
    const manifest = json(manifestPath);
    manifest.components.agents = ["../marketplace.json"];
    manifest.signature = "ed25519:forged";
    save(manifestPath, manifest);
    result = validateMarketplace(copy);
    assert.ok(result.problems.some((problem) => problem.message.includes("invalid or missing agents component")));
    assert.ok(result.problems.some((problem) => problem.message.includes("signature is deprecated")));
  });
});

test("rejects missing execution disclosure and arbitrary lifecycle commands", () => {
  temporaryRepository((copy) => {
    const pdfPath = join(copy, "artifacts", "skill-pdf", "chainabit-plugin.json");
    const pdf = json(pdfPath);
    pdf.permissions.requested = [];
    pdf.install = { postInstall: "npm install -g attacker" };
    save(pdfPath, pdf);
    let result = validateMarketplace(copy);
    assert.ok(result.problems.some((problem) => problem.message.includes("sandbox.execute")));
    assert.ok(result.problems.some((problem) => problem.message.includes("raw postInstall")));

    pdf.permissions.requested = ["sandbox.execute"];
    pdf.permissions.execute = false;
    delete pdf.install;
    save(pdfPath, pdf);
    result = validateMarketplace(copy);
    assert.ok(result.problems.some((problem) => problem.message.includes("scripts and requires permissions.execute")));

    delete pdf.permissions.requested;
    pdf.permissions.execute = true;
    save(pdfPath, pdf);
    result = validateMarketplace(copy);
    assert.ok(result.problems.some((problem) => problem.message.includes("permissions.requested must be an explicit array")));
  });
});

test("supports safe assets and rejects executable or malformed asset content", () => {
  assert.equal(validateAssetBytes("assets/icon.png", Buffer.from("89504e470d0a1a0a", "hex")), null);
  assert.match(validateAssetBytes("assets/icon.exe", Buffer.from("MZ")), /unsupported/);
  assert.match(validateAssetBytes("assets/icon.svg", Buffer.from("<svg><script>alert(1)</script></svg>")), /executable/);
});

test("resolves dependencies deterministically and rejects cycles and incompatible ranges", () => {
  const manifests = new Map([
    ["skill-base", { manifest: { id: "skill-base", version: "1.2.0", composition: { requires: [] } } }],
    ["skill-app", { manifest: { id: "skill-app", version: "1.0.0", composition: { requires: [{ id: "skill-base", version: "^1.0.0" }] } } }],
  ]);
  assert.deepEqual(resolveCompositionGraph(manifests, ["skill-app"]), ["skill-base", "skill-app"]);
  assert.deepEqual(dependencyIdAndConstraint("skill-base"), { id: "skill-base", constraint: null });
  manifests.get("skill-base").manifest.version = "2.0.0";
  assert.throws(() => resolveCompositionGraph(manifests, ["skill-app"]), /dependency conflict/);
  manifests.get("skill-base").manifest.version = "1.2.0";
  manifests.get("skill-base").manifest.composition.requires = ["skill-app"];
  assert.throws(() => resolveCompositionGraph(manifests, ["skill-app"]), /dependency cycle/);
});

test("package digests are content identities and are deterministic", () => {
  const first = packageDigest(join(root, "artifacts", "skill-pdf"));
  const second = packageDigest(join(root, "artifacts", "skill-pdf"));
  assert.equal(first, second);
  assert.deepEqual(permissionErrors(json(join(root, "artifacts", "skill-pdf", "chainabit-plugin.json"))), []);
});

test("artifact skill instructions advertise every registered production generator", () => {
  const { manifests } = validateMarketplace(root);
  for (const [id, entry] of manifests) {
    const generators = entry.manifest.artifactContract?.generators ?? [];
    if (generators.length === 0) continue;
    const skillRoots = entry.manifest.components?.skills ?? [];
    assert.equal(skillRoots.length, 1, `${id} must have one authoritative artifact skill`);
    const skillRoot = skillRoots[0];
    const instructions = readFileSync(join(entry.absolute, skillRoot, "SKILL.md"), "utf8");
    assert.match(
      instructions,
      new RegExp(`^  version: ${entry.manifest.version.replace(/\\./g, "\\.")}$`, "m"),
      `${id} skill metadata must match its release version`,
    );
    for (const generator of generators) {
      const relativeEntrypoint = generator.entrypoint.slice(`${skillRoot}/`.length);
      assert.match(
        instructions,
        new RegExp(`python3\\s+${relativeEntrypoint.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`),
        `${id} instructions must directly advertise registered generator ${relativeEntrypoint}`,
      );
    }
  }
});
