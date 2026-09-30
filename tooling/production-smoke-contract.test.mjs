import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const workflow = readFileSync(
  join(root, ".github/workflows/production-artifact-smoke.yml"),
  "utf8",
);
const smoke = readFileSync(
  join(root, "tooling/pdf-production-sandbox-smoke.mjs"),
  "utf8",
);

test("the production artifact smoke stays scheduled and manually runnable", () => {
  assert.match(workflow, /workflow_dispatch:/);
  assert.match(workflow, /schedule:/);
  assert.match(workflow, /node tooling\/pdf-production-sandbox-smoke\.mjs/);
  assert.match(workflow, /node tooling\/verify-api-system-skill-pins\.mjs/);
  assert.ok(
    workflow.indexOf("verify-api-system-skill-pins.mjs") <
      workflow.indexOf("pdf-production-sandbox-smoke.mjs"),
  );
  assert.match(workflow, /CLOUDFLARE_SANDBOX_API_URL:/);
  assert.match(workflow, /CLOUDFLARE_SANDBOX_API_KEY:/);
});

test("the production smoke cannot lose the two incident regressions silently", () => {
  assert.match(smoke, /const ESCAPED_SOURCE = String\.raw/);
  assert.match(smoke, /const FOUR_PAGE_SOURCE =/);
  assert.match(smoke, /evidence\.escapedRejection/);
  assert.match(smoke, /error\.code === 'invalid_input'/);
  assert.match(smoke, /evidence\.lengthRejection/);
  assert.match(smoke, /'--pages',\s*'1'/);
  assert.match(smoke, /renders to 4 pages but 1 were requested/);
});

test("the smoke records bounded runtime evidence and always destroys its sandbox", () => {
  assert.match(smoke, /smokeStartedAt = performance\.now\(\)/);
  assert.match(smoke, /durationMs/);
  assert.match(smoke, /timeout_ms: timeoutMs/);
  assert.match(smoke, /method: 'DELETE'/);
});
