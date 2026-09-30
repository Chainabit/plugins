#!/usr/bin/env node
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const RELEASE_PATTERN =
  /\n\s*pluginId:\s*'([^']+)',[\s\S]*?\n\s*skillSlug:\s*'([^']+)',[\s\S]*?\n\s*version:\s*'([^']+)',[\s\S]*?\n\s*revision:\s*'([a-f0-9]{40})',[\s\S]*?\n\s*packageSha256:\s*\n?\s*'([a-f0-9]{64})',[\s\S]*?\n\s*source:\s*'([^']+)',/g;
const apiCatalogUrl =
  process.env.CHAINABIT_API_SYSTEM_SKILL_CATALOG_URL ??
  "https://raw.githubusercontent.com/Chainabit/chainabit-api/main/libs/domains/plugins/src/feature/plugins/marketplace/system-skill-release.catalog.ts";
const root = join(dirname(fileURLToPath(import.meta.url)), "..");

export function parseApiSystemSkillPins(source) {
  const releases = [...source.matchAll(RELEASE_PATTERN)].map((match) => ({
    pluginId: match[1],
    skillSlug: match[2],
    version: match[3],
    revision: match[4],
    packageSha256: match[5],
    source: match[6],
  }));
  const declaredCount = (source.match(/\n\s*pluginId:\s*'/g) ?? []).length;
  if (releases.length === 0 || releases.length !== declaredCount) {
    throw new Error(
      `Could not read every API system-skill pin (${releases.length}/${declaredCount})`,
    );
  }
  return releases;
}

export function apiPinDrift(pins, marketplace) {
  const published = new Map(
    marketplace.plugins.map((plugin) => [plugin.id, plugin]),
  );
  const problems = [];
  for (const pin of pins) {
    const plugin = published.get(pin.pluginId);
    if (!plugin) {
      problems.push(`${pin.pluginId}: absent from marketplace`);
      continue;
    }
    if (plugin.source !== pin.source) {
      problems.push(`${pin.pluginId}: source drift`);
    }
    if (plugin.version !== pin.version) {
      problems.push(
        `${pin.pluginId}: API pins ${pin.version}, marketplace publishes ${plugin.version}`,
      );
    }
    if (plugin.integrity?.packageSha256 !== pin.packageSha256) {
      problems.push(`${pin.pluginId}: package identity drift`);
    }
  }
  return problems;
}

export async function verifyApiSystemSkillPins() {
  const response = await fetch(apiCatalogUrl, {
    headers: { "User-Agent": "chainabit-plugin-release-consistency/1.0" },
    signal: AbortSignal.timeout(30_000),
  });
  if (!response.ok) {
    throw new Error(
      `API system-skill catalog returned HTTP ${response.status}`,
    );
  }
  const pins = parseApiSystemSkillPins(await response.text());
  const marketplace = JSON.parse(
    readFileSync(join(root, "marketplace.json"), "utf8"),
  );
  const problems = apiPinDrift(pins, marketplace);
  if (problems.length) throw new Error(problems.join("\n"));
  return { apiPins: pins.length, marketplaceDrift: 0 };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  try {
    console.log(JSON.stringify(await verifyApiSystemSkillPins(), null, 2));
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  }
}
