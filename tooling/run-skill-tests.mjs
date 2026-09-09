#!/usr/bin/env node

// Runs the executable tests a skill ships, so shipped behaviour is verified
// rather than merely packaged.
//
// A skill's `tests/` used to be inert bundle payload: real test files that
// nothing ever executed. That is worse than no tests, because the directory
// reads as coverage. Any skill whose bundle carries `tests/test_*.py` is run
// here, with the interpreter its manifest declares.
//
// Discovery is by convention rather than configuration on purpose -- a skill
// that adds tests is picked up without editing this file or a workflow, which
// is the only way a convention like this survives contact with contributors.

import { execFileSync } from "node:child_process";
import { existsSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const TEST_DIRECTORY = "tests";
const TEST_PREFIX = "test_";

/** Every `<category>/<plugin>/skills/<skill>/tests` holding python tests. */
export function findSkillTestDirectories(root) {
  const found = [];
  for (const category of directoriesIn(root)) {
    for (const plugin of directoriesIn(join(root, category))) {
      const skillsRoot = join(root, category, plugin, "skills");
      if (!existsSync(skillsRoot)) continue;
      for (const skill of directoriesIn(skillsRoot)) {
        const tests = join(skillsRoot, skill, TEST_DIRECTORY);
        if (!existsSync(tests) || !statSync(tests).isDirectory()) continue;
        const hasTests = readdirSync(tests).some(
          (name) => name.startsWith(TEST_PREFIX) && name.endsWith(".py"),
        );
        if (hasTests) found.push(tests);
      }
    }
  }
  return found.sort();
}

function directoriesIn(path) {
  if (!existsSync(path)) return [];
  return readdirSync(path, { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && !entry.name.startsWith("."))
    .map((entry) => entry.name)
    .sort();
}

function main() {
  const root = join(dirname(fileURLToPath(import.meta.url)), "..");
  const directories = findSkillTestDirectories(root);

  if (directories.length === 0) {
    console.log("No skill ships executable tests.\n");
    return;
  }

  const failed = [];
  for (const directory of directories) {
    const where = relative(root, directory);
    console.log(`\n== ${where}`);
    try {
      execFileSync("python3", ["-m", "unittest", "discover", "-s", directory], {
        cwd: root,
        stdio: "inherit",
      });
    } catch {
      failed.push(where);
    }
  }

  console.log(
    `\nRan executable tests for ${directories.length} skill(s).\n`,
  );
  if (failed.length) {
    console.error(`Failing skill test suites:\n  ${failed.join("\n  ")}\n`);
    process.exitCode = 1;
  }
}

if (pathToFileURL(process.argv[1] ?? "").href === import.meta.url) main();
