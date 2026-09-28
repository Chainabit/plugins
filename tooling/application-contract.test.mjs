import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { applicationCapabilityErrors } from './marketplace-contract.mjs';
const json = path => JSON.parse(readFileSync(new URL(path, import.meta.url), 'utf8'));

for (const path of ['../languages/skill-javascript', '../languages/skill-python', '../web/skill-react', '../web/skill-angular']) {
  test(`${path} exposes a bounded application contract independently of directory layout`, () => {
    const manifest = json(`${path}/chainabit-plugin.json`);
    assert.deepEqual(applicationCapabilityErrors(manifest.application), []);
    assert.equal(manifest.permissions.execute, false);
    assert(manifest.composition.requires.includes('skill-brand-defaults'));
    assert(manifest.composition.requires.includes('skill-project-bootstrap'));
  });
}
test('new technology identities require no centralized framework registration', () => {
  const facet = json('../web/skill-react/chainabit-plugin.json').application;
  assert.deepEqual(applicationCapabilityErrors({...facet, technologyId: 'future.custom-ecosystem'}), []);
});
test('declarations cannot supply source trees, palettes, or network authority', () => {
  const facet = json('../web/skill-react/chainabit-plugin.json').application;
  for (const mutation of [{...facet, tree: {}}, {...facet, palette: 'blue'}, {...facet, dependencyPolicy: 'unrestricted'}, {...facet, operations: ['build', 'build']}])
    assert(applicationCapabilityErrors(mutation).length > 0);
});
test('shared execution plan schema is identical to the bundled reference', () => {
  const canonical = json('../spec/application-plan.schema.json');
  assert.deepEqual(json('../foundations/skill-project-bootstrap/skills/project-bootstrap/references/application-plan.schema.json'), canonical);
  const relativePath = new RegExp(canonical.$defs.relativePath.pattern);
  for (const value of ['.', '..', './entry', '../entry', '/entry', 'app//entry', 'app/', 'app\\entry', 'app\nentry']) assert.equal(relativePath.test(value), false, value);
  for (const value of ['chosen/entry.mjs', '.config/compiler.json', 'different-layout/start.ts']) assert.equal(relativePath.test(value), true, value);
});
