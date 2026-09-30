import assert from "node:assert/strict";
import test from "node:test";
import {
  apiPinDrift,
  parseApiSystemSkillPins,
} from "./verify-api-system-skill-pins.mjs";

const pinSource = ({
  pluginId = "skill-example",
  version = "1.2.3",
  digest = "a".repeat(64),
} = {}) => `
  {
    pluginId: '${pluginId}',
    skillSlug: '${pluginId}-example',
    version: '${version}',
    revision: '${"b".repeat(40)}',
    packageSha256:
      '${digest}',
    source: 'https://github.com/chainabit/plugins.git#example/${pluginId}',
  },`;

test("reads every API pin exactly once", () => {
  const pins = parseApiSystemSkillPins(
    `${pinSource()}${pinSource({ pluginId: "skill-two" })}`,
  );
  assert.equal(pins.length, 2);
  assert.deepEqual(
    pins.map((pin) => pin.pluginId),
    ["skill-example", "skill-two"],
  );
});

test("refuses partial API catalog parsing", () => {
  assert.throws(
    () =>
      parseApiSystemSkillPins(
        `${pinSource()}\n  {\n    pluginId: 'skill-incomplete',\n  },`,
      ),
    /Could not read every API system-skill pin \(1\/2\)/,
  );
});

test("reports stale, republished, and missing API pins", () => {
  const [pin] = parseApiSystemSkillPins(pinSource());
  const marketplace = {
    plugins: [
      {
        id: pin.pluginId,
        source: pin.source,
        version: "9.9.9",
        integrity: { packageSha256: "0".repeat(64) },
      },
    ],
  };
  const problems = apiPinDrift(
    [pin, { ...pin, pluginId: "skill-missing" }],
    marketplace,
  );
  assert.ok(problems.some((problem) => problem.includes("API pins")));
  assert.ok(problems.some((problem) => problem.includes("package identity")));
  assert.ok(problems.some((problem) => problem.includes("absent")));
});

test("accepts matching immutable identities", () => {
  const pins = parseApiSystemSkillPins(pinSource());
  const marketplace = {
    plugins: pins.map((pin) => ({
      id: pin.pluginId,
      source: pin.source,
      version: pin.version,
      integrity: { packageSha256: pin.packageSha256 },
    })),
  };
  assert.deepEqual(apiPinDrift(pins, marketplace), []);
});
