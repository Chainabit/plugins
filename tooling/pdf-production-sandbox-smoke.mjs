#!/usr/bin/env node
import { readFile, readdir } from 'node:fs/promises';
import { resolve, relative, sep } from 'node:path';

const baseUrl = process.env.CLOUDFLARE_SANDBOX_API_URL?.replace(/\/$/, '');
const apiKey = process.env.CLOUDFLARE_SANDBOX_API_KEY;
if (!baseUrl || !apiKey) throw new Error('production sandbox configuration is unavailable');

const headers = {
  Authorization: `Bearer ${apiKey}`,
  ...(process.env.CLOUDFLARE_SANDBOX_ACCESS_CLIENT_ID
    ? { 'CF-Access-Client-Id': process.env.CLOUDFLARE_SANDBOX_ACCESS_CLIENT_ID }
    : {}),
  ...(process.env.CLOUDFLARE_SANDBOX_ACCESS_CLIENT_SECRET
    ? { 'CF-Access-Client-Secret': process.env.CLOUDFLARE_SANDBOX_ACCESS_CLIENT_SECRET }
    : {}),
};
const skillRoot = resolve(process.argv[2] ?? 'artifacts/skill-pdf/skills/pdf');

async function request(path, init = {}) {
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    headers: { ...headers, ...(init.headers ?? {}) },
  });
  if (!response.ok) throw new Error(`${init.method ?? 'GET'} ${path} returned ${response.status}`);
  return response;
}

async function filesUnder(root) {
  const result = [];
  for (const entry of await readdir(root, { withFileTypes: true })) {
    if (entry.name === '__pycache__' || entry.name.endsWith('.pyc')) continue;
    const path = resolve(root, entry.name);
    if (entry.isDirectory()) result.push(...(await filesUnder(path)));
    else result.push(path);
  }
  return result;
}

function parseSse(text) {
  let event = '';
  let data = '';
  const result = { stdout: '', stderr: '', exitCode: null, error: null };
  const flush = () => {
    if (!event || !data) return;
    if (event === 'stdout' || event === 'stderr') {
      result[event] += Buffer.from(data, 'base64').toString('utf8');
    } else if (event === 'exit') result.exitCode = JSON.parse(data).exit_code;
    else if (event === 'error') result.error = JSON.parse(data);
    event = ''; data = '';
  };
  for (const line of `${text}\n`.split(/\r?\n/)) {
    if (!line) flush();
    else if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) data += line.slice(5).trim();
  }
  return result;
}

async function exec(sandboxId, argv, timeoutMs = 120_000) {
  const response = await request(`/v1/sandbox/${sandboxId}/exec`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ argv, cwd: '/workspace', timeout_ms: timeoutMs }),
  });
  return parseSse(await response.text());
}

function assert(condition, message, evidence) {
  if (!condition) throw new Error(`${message}: ${JSON.stringify(evidence).slice(0, 2000)}`);
}

function lastJsonLine(text) {
  const line = text.trim().split(/\r?\n/).reverse().find((candidate) => candidate.trim().startsWith('{'));
  if (!line) throw new Error(`structured protocol frame is missing: ${text.slice(-1000)}`);
  return JSON.parse(line);
}

const created = await request('/v1/sandbox', { method: 'POST' });
const { id: sandboxId } = await created.json();
const evidence = {};
try {
  for (const localPath of await filesUnder(skillRoot)) {
    const rel = relative(skillRoot, localPath).split(sep).join('/');
    await request(`/v1/sandbox/${sandboxId}/file/workspace/.skills/skill-pdf-pdf/${rel}`, {
      method: 'PUT',
      headers: { 'content-type': 'application/octet-stream' },
      body: await readFile(localPath),
    });
  }
  const fixture = await readFile(resolve(skillRoot, 'tests/fixtures/turkish.md'));
  await request(`/v1/sandbox/${sandboxId}/file/workspace/report.md`, {
    method: 'PUT', headers: { 'content-type': 'application/octet-stream' }, body: fixture,
  });
  await request(`/v1/sandbox/${sandboxId}/file/workspace/empty.md`, {
    method: 'PUT', headers: { 'content-type': 'application/octet-stream' }, body: '',
  });

  evidence.runtime = await exec(sandboxId, ['python3', '-c', 'import glob,json,locale,os,pypdf,subprocess,weasyprint; font_dir=os.environ.get("CHAINABIT_ARTIFACT_FONT_DIR",""); print(json.dumps({"python":__import__("sys").version.split()[0],"pypdf":pypdf.__version__,"weasyprint":weasyprint.__version__,"locale":locale.getpreferredencoding(False),"fontDir":font_dir,"fontFiles":[os.path.basename(p) for p in sorted(glob.glob(font_dir+"/*"))],"fontMatch":subprocess.run(["fc-match","IBM Plex Sans"],capture_output=True,text=True).stdout.strip()}))']);
  assert(evidence.runtime.exitCode === 0, 'runtime profile probe failed', evidence.runtime);

  evidence.render = await exec(sandboxId, ['python3', '.skills/skill-pdf-pdf/scripts/md_to_pdf.py', 'report.md', 'report.pdf', '--lang', 'tr']);
  assert(evidence.render.exitCode === 0, 'official renderer failed', {
    runtime: evidence.runtime,
    render: evidence.render,
  });
  const rendered = lastJsonLine(evidence.render.stdout);
  assert(rendered.schema === 'chainabit.pdf.execution/v1', 'renderer protocol mismatch', rendered);

  evidence.validate = await exec(sandboxId, ['python3', '.skills/skill-pdf-pdf/scripts/validate_pdf.py', 'report.pdf']);
  assert(evidence.validate.exitCode === 0, 'authoritative validator failed', evidence.validate);
  const validated = lastJsonLine(evidence.validate.stdout);
  assert(validated.subject.sha256 === rendered.output.sha256, 'render/validation hash mismatch', { rendered, validated });
  assert(validated.subject.mime === 'application/pdf', 'validated MIME mismatch', validated);
  assert(validated.subject.shape === 'file' && rendered.output.shape === 'file', 'PDF output shape mismatch', { rendered, validated });
  assert(validated.fonts.length > 0, 'PDF has no embedded font evidence', validated);

  evidence.inputRejection = await exec(sandboxId, ['python3', '.skills/skill-pdf-pdf/scripts/md_to_pdf.py', 'empty.md', 'empty.pdf']);
  assert(evidence.inputRejection.exitCode === 1, 'invalid input was not exit 1', evidence.inputRejection);

  evidence.missingDependency = await exec(sandboxId, ['python3', '-S', '.skills/skill-pdf-pdf/scripts/md_to_pdf.py', 'report.md', 'missing.pdf', '--lang', 'tr']);
  assert(evidence.missingDependency.exitCode === 2, 'missing dependency was not exit 2', evidence.missingDependency);
  assert(lastJsonLine(evidence.missingDependency.stderr).error.class === 'missing_runtime_dependency', 'missing dependency class mismatch', evidence.missingDependency);

  evidence.blankFixture = await exec(sandboxId, ['python3', '-c', 'from pypdf import PdfWriter; w=PdfWriter(); w.add_blank_page(width=595,height=842); f=open("blank.pdf","wb"); w.write(f); f.close()']);
  assert(evidence.blankFixture.exitCode === 0, 'blank fixture creation failed', evidence.blankFixture);
  evidence.blankRejection = await exec(sandboxId, ['python3', '.skills/skill-pdf-pdf/scripts/validate_pdf.py', 'blank.pdf']);
  assert(evidence.blankRejection.exitCode === 1, 'parseable blank PDF was accepted', evidence.blankRejection);

  evidence.timeout = await exec(sandboxId, ['python3', '-c', 'import time; time.sleep(2)'], 50);
  assert(evidence.timeout.error || evidence.timeout.exitCode !== 0, 'timeout was reported as success', evidence.timeout);

  console.log(JSON.stringify({
    ok: true,
    sandboxRuntime: JSON.parse(evidence.runtime.stdout.trim()),
    artifact: validated.subject,
    generator: rendered.generator,
    inputRejectionExit: evidence.inputRejection.exitCode,
    dependencyFailureExit: evidence.missingDependency.exitCode,
    blankRejectionExit: evidence.blankRejection.exitCode,
    timeoutObserved: Boolean(evidence.timeout.error || evidence.timeout.exitCode !== 0),
  }, null, 2));
} finally {
  await fetch(`${baseUrl}/v1/sandbox/${sandboxId}`, { method: 'DELETE', headers }).catch(() => undefined);
}
