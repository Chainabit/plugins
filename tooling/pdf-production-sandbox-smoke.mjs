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
    ? {
        'CF-Access-Client-Secret': process.env.CLOUDFLARE_SANDBOX_ACCESS_CLIENT_SECRET,
      }
    : {}),
};
const smokeStartedAt = performance.now();
const skillRoot = resolve(process.argv[2] ?? 'artifacts/skill-pdf/skills/pdf');
const pptxSkillRoot = resolve('artifacts/skill-pptx/skills/pptx');
const docxSkillRoot = resolve('artifacts/skill-docx/skills/docx');
const xlsxSkillRoot = resolve('artifacts/skill-xlsx/skills/xlsx');
const websiteSkillRoot = resolve('web/skill-static-website/skills/static-website');

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

async function materializeSkill(sandboxId, localRoot, remoteDirectory) {
  for (const localPath of await filesUnder(localRoot)) {
    const rel = relative(localRoot, localPath).split(sep).join('/');
    await request(`/v1/sandbox/${sandboxId}/file/workspace/.skills/${remoteDirectory}/${rel}`, {
      method: 'PUT',
      headers: { 'content-type': 'application/octet-stream' },
      body: await readFile(localPath),
    });
  }
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
    event = '';
    data = '';
  };
  for (const line of `${text}\n`.split(/\r?\n/)) {
    if (!line) flush();
    else if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) data += line.slice(5).trim();
  }
  return result;
}

async function exec(sandboxId, argv, timeoutMs = 120_000) {
  const startedAt = performance.now();
  const response = await request(`/v1/sandbox/${sandboxId}/exec`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ argv, cwd: '/workspace', timeout_ms: timeoutMs }),
  });
  return {
    ...parseSse(await response.text()),
    durationMs: Math.round(performance.now() - startedAt),
  };
}

function assert(condition, message, evidence) {
  if (!condition) throw new Error(`${message}: ${JSON.stringify(evidence).slice(0, 2000)}`);
}

function lastJsonLine(text) {
  const line = text
    .trim()
    .split(/\r?\n/)
    .reverse()
    .find((candidate) => candidate.trim().startsWith('{'));
  if (!line) throw new Error(`structured protocol frame is missing: ${text.slice(-1000)}`);
  return JSON.parse(line);
}

const sandboxStartupStartedAt = performance.now();
const created = await request('/v1/sandbox', { method: 'POST' });
const { id: sandboxId } = await created.json();
const evidence = {
  sandboxStartupMs: Math.round(performance.now() - sandboxStartupStartedAt),
};
let report;
try {
  const skillPreparationStartedAt = performance.now();
  await materializeSkill(sandboxId, skillRoot, 'skill-pdf-pdf');
  await materializeSkill(sandboxId, pptxSkillRoot, 'skill-pptx-pptx');
  await materializeSkill(sandboxId, docxSkillRoot, 'skill-docx-docx');
  await materializeSkill(sandboxId, xlsxSkillRoot, 'skill-xlsx-xlsx');
  await materializeSkill(sandboxId, websiteSkillRoot, 'skill-static-website-static-website');
  evidence.skillPreparationMs = Math.round(performance.now() - skillPreparationStartedAt);
  const fixture = await readFile(resolve(skillRoot, 'tests/fixtures/turkish.md'));
  await request(`/v1/sandbox/${sandboxId}/file/workspace/report.md`, {
    method: 'PUT',
    headers: { 'content-type': 'application/octet-stream' },
    body: fixture,
  });
  await request(`/v1/sandbox/${sandboxId}/file/workspace/empty.md`, {
    method: 'PUT',
    headers: { 'content-type': 'application/octet-stream' },
    body: '',
  });

  evidence.runtime = await exec(sandboxId, [
    'python3',
    '-c',
    'import glob,json,locale,os,pypdf,subprocess,weasyprint; font_dir=os.environ.get("CHAINABIT_ARTIFACT_FONT_DIR",""); print(json.dumps({"python":__import__("sys").version.split()[0],"pypdf":pypdf.__version__,"weasyprint":weasyprint.__version__,"locale":locale.getpreferredencoding(False),"fontDir":font_dir,"fontFiles":[os.path.basename(p) for p in sorted(glob.glob(font_dir+"/*"))],"fontMatch":subprocess.run(["fc-match","IBM Plex Sans"],capture_output=True,text=True).stdout.strip()}))',
  ]);
  assert(evidence.runtime.exitCode === 0, 'runtime profile probe failed', evidence.runtime);

  evidence.render = await exec(sandboxId, [
    'python3',
    '.skills/skill-pdf-pdf/scripts/md_to_pdf.py',
    'report.md',
    'report.pdf',
    '--lang',
    'tr',
  ]);
  assert(evidence.render.exitCode === 0, 'official renderer failed', {
    runtime: evidence.runtime,
    render: evidence.render,
  });
  const rendered = lastJsonLine(evidence.render.stdout);
  assert(rendered.schema === 'chainabit.pdf.execution/v1', 'renderer protocol mismatch', rendered);

  evidence.validate = await exec(sandboxId, ['python3', '.skills/skill-pdf-pdf/scripts/validate_pdf.py', 'report.pdf']);
  assert(evidence.validate.exitCode === 0, 'authoritative validator failed', evidence.validate);
  const validated = lastJsonLine(evidence.validate.stdout);
  assert(validated.subject.sha256 === rendered.output.sha256, 'render/validation hash mismatch', {
    rendered,
    validated,
  });
  assert(validated.subject.mime === 'application/pdf', 'validated MIME mismatch', validated);
  assert(validated.subject.shape === 'file' && rendered.output.shape === 'file', 'PDF output shape mismatch', {
    rendered,
    validated,
  });
  assert(validated.fonts.length > 0, 'PDF has no embedded font evidence', validated);

  evidence.inputRejection = await exec(sandboxId, [
    'python3',
    '.skills/skill-pdf-pdf/scripts/md_to_pdf.py',
    'empty.md',
    'empty.pdf',
  ]);
  assert(evidence.inputRejection.exitCode === 1, 'invalid input was not exit 1', evidence.inputRejection);

  evidence.missingDependency = await exec(sandboxId, [
    'python3',
    '-S',
    '.skills/skill-pdf-pdf/scripts/md_to_pdf.py',
    'report.md',
    'missing.pdf',
    '--lang',
    'tr',
  ]);
  assert(evidence.missingDependency.exitCode === 2, 'missing dependency was not exit 2', evidence.missingDependency);
  assert(
    lastJsonLine(evidence.missingDependency.stderr).error.class === 'missing_runtime_dependency',
    'missing dependency class mismatch',
    evidence.missingDependency,
  );

  evidence.blankFixture = await exec(sandboxId, [
    'python3',
    '-c',
    'from pypdf import PdfWriter; w=PdfWriter(); w.add_blank_page(width=595,height=842); f=open("blank.pdf","wb"); w.write(f); f.close()',
  ]);
  assert(evidence.blankFixture.exitCode === 0, 'blank fixture creation failed', evidence.blankFixture);
  evidence.blankRejection = await exec(sandboxId, [
    'python3',
    '.skills/skill-pdf-pdf/scripts/validate_pdf.py',
    'blank.pdf',
  ]);
  assert(evidence.blankRejection.exitCode === 1, 'parseable blank PDF was accepted', evidence.blankRejection);

  evidence.timeout = await exec(sandboxId, ['python3', '-c', 'import time; time.sleep(2)'], 50);
  assert(
    evidence.timeout.error || evidence.timeout.exitCode !== 0,
    'timeout was reported as success',
    evidence.timeout,
  );

  const deckSpec = JSON.stringify({
    title: 'Chainabit Artifact Contract',
    slides: [
      {
        layout: 'title',
        title: 'Güvenilir Çıktılar',
        subtitle: 'ğüşöçıİĞÜŞÖÇ · مرحبا بالعالم',
      },
      {
        layout: 'content',
        title: 'Doğrulama Zinciri',
        bullets: ['Üret', 'Aynı byte kimliğini doğrula', 'Yalnızca doğrulanmış çıktıyı yayımla'],
      },
    ],
  });
  await request(`/v1/sandbox/${sandboxId}/file/workspace/deck.json`, {
    method: 'PUT',
    headers: { 'content-type': 'application/octet-stream' },
    body: deckSpec,
  });
  evidence.pptxRender = await exec(sandboxId, [
    'python3',
    '.skills/skill-pptx-pptx/scripts/deck_pptx.py',
    'deck.json',
    'deck.pptx',
  ]);
  assert(evidence.pptxRender.exitCode === 0, 'official PPTX generator failed', evidence.pptxRender);
  evidence.pptxValidate = await exec(sandboxId, [
    'python3',
    '.skills/skill-pptx-pptx/scripts/validate_pptx.py',
    'deck.pptx',
  ]);
  assert(evidence.pptxValidate.exitCode === 0, 'authoritative PPTX validator failed', evidence.pptxValidate);
  const renderedPptx = lastJsonLine(evidence.pptxRender.stdout);
  const validatedPptx = lastJsonLine(evidence.pptxValidate.stdout);
  assert(renderedPptx.output.sha256 === validatedPptx.subject.sha256, 'PPTX render/validation hash mismatch', {
    renderedPptx,
    validatedPptx,
  });
  assert(validatedPptx.typography.family === 'IBM Plex Sans', 'PPTX default font mismatch', validatedPptx);

  const documentSpec = JSON.stringify({
    properties: { title: 'Chainabit Saha Raporu', author: 'Chainabit' },
    blocks: [
      { type: 'heading', text: 'Güvenilir Çıktılar', level: 1 },
      { type: 'paragraph', text: 'ğüşöçıİĞÜŞÖÇ · مرحبا بالعالم' },
      { type: 'bullets', items: ['Üret', 'Doğrula', 'Yayımla'] },
      {
        type: 'table',
        columns: ['Bölge', 'Adet'],
        rows: [
          ['İstanbul', '4210'],
          ['Şanlıurfa', '806'],
        ],
      },
    ],
  });
  await request(`/v1/sandbox/${sandboxId}/file/workspace/document.json`, {
    method: 'PUT',
    headers: { 'content-type': 'application/octet-stream' },
    body: documentSpec,
  });
  evidence.docxRender = await exec(sandboxId, [
    'python3',
    '.skills/skill-docx-docx/scripts/build_docx.py',
    'document.json',
    'document.docx',
  ]);
  assert(evidence.docxRender.exitCode === 0, 'official DOCX generator failed', evidence.docxRender);
  evidence.docxValidate = await exec(sandboxId, [
    'python3',
    '.skills/skill-docx-docx/scripts/validate_docx.py',
    'document.docx',
    '--strict',
  ]);
  assert(evidence.docxValidate.exitCode === 0, 'authoritative DOCX validator failed', evidence.docxValidate);
  const renderedDocx = lastJsonLine(evidence.docxRender.stdout);
  const validatedDocx = lastJsonLine(evidence.docxValidate.stdout);
  assert(renderedDocx.output.sha256 === validatedDocx.subject.sha256, 'DOCX render/validation hash mismatch', {
    renderedDocx,
    validatedDocx,
  });
  assert(validatedDocx.typography.family === 'IBM Plex Sans', 'DOCX default font mismatch', validatedDocx);

  const workbookSpec = JSON.stringify({
    properties: { title: 'Bölge Satışları', creator: 'Chainabit' },
    sheets: [
      {
        name: 'Satışlar',
        columns: [
          { header: 'Bölge', key: 'region', type: 'text', width: 22 },
          { header: 'Adet', key: 'units', type: 'integer', format: '#,##0' },
          {
            header: 'Ciro',
            key: 'gross',
            type: 'number',
            format: '#,##0.00 ₺',
          },
        ],
        rows: [
          { region: 'İstanbul', units: 4210, gross: 918450.5 },
          { region: 'Şanlıurfa', units: 806, gross: 151900.25 },
        ],
      },
    ],
  });
  await request(`/v1/sandbox/${sandboxId}/file/workspace/workbook.json`, {
    method: 'PUT',
    headers: { 'content-type': 'application/octet-stream' },
    body: workbookSpec,
  });
  evidence.xlsxRender = await exec(sandboxId, [
    'python3',
    '.skills/skill-xlsx-xlsx/scripts/build_xlsx.py',
    'workbook.json',
    'workbook.xlsx',
  ]);
  assert(evidence.xlsxRender.exitCode === 0, 'official XLSX generator failed', evidence.xlsxRender);
  evidence.xlsxValidate = await exec(sandboxId, [
    'python3',
    '.skills/skill-xlsx-xlsx/scripts/validate_xlsx.py',
    'workbook.xlsx',
    '--strict',
  ]);
  assert(evidence.xlsxValidate.exitCode === 0, 'authoritative XLSX validator failed', evidence.xlsxValidate);
  const renderedXlsx = lastJsonLine(evidence.xlsxRender.stdout);
  const validatedXlsx = lastJsonLine(evidence.xlsxValidate.stdout);
  assert(renderedXlsx.output.sha256 === validatedXlsx.subject.sha256, 'XLSX render/validation hash mismatch', {
    renderedXlsx,
    validatedXlsx,
  });
  assert(validatedXlsx.typography.family === 'IBM Plex Sans', 'XLSX default font mismatch', validatedXlsx);

  evidence.websiteRender = await exec(sandboxId, [
    'python3',
    '.skills/skill-static-website-static-website/scripts/scaffold_site.py',
    '--template',
    'portfolio',
    'site',
  ]);
  assert(evidence.websiteRender.exitCode === 0, 'official website generator failed', evidence.websiteRender);
  evidence.websiteValidate = await exec(sandboxId, [
    'python3',
    '.skills/skill-static-website-static-website/scripts/validate_site.py',
    'site',
    '--strict',
  ]);
  assert(evidence.websiteValidate.exitCode === 0, 'authoritative website validator failed', evidence.websiteValidate);
  const renderedWebsite = lastJsonLine(evidence.websiteRender.stdout);
  const validatedWebsite = lastJsonLine(evidence.websiteValidate.stdout);
  assert(
    renderedWebsite.output.sha256 === validatedWebsite.subject.sha256,
    'website render/validation tree hash mismatch',
    { renderedWebsite, validatedWebsite },
  );
  assert(
    validatedWebsite.checks.typography.family === 'IBM Plex Sans',
    'website default font mismatch',
    validatedWebsite,
  );

  report = {
    ok: true,
    sandboxRuntime: JSON.parse(evidence.runtime.stdout.trim()),
    artifact: validated.subject,
    generator: rendered.generator,
    inputRejectionExit: evidence.inputRejection.exitCode,
    dependencyFailureExit: evidence.missingDependency.exitCode,
    blankRejectionExit: evidence.blankRejection.exitCode,
    timeoutObserved: Boolean(evidence.timeout.error || evidence.timeout.exitCode !== 0),
    pptx: validatedPptx.subject,
    docx: validatedDocx.subject,
    xlsx: validatedXlsx.subject,
    website: validatedWebsite.subject,
    timingsMs: {
      sandboxStartup: evidence.sandboxStartupMs,
      skillPreparation: evidence.skillPreparationMs,
      runtimeProbe: evidence.runtime.durationMs,
      pdfGeneration: evidence.render.durationMs,
      pdfValidation: evidence.validate.durationMs,
      pptxGeneration: evidence.pptxRender.durationMs,
      pptxValidation: evidence.pptxValidate.durationMs,
      docxGeneration: evidence.docxRender.durationMs,
      docxValidation: evidence.docxValidate.durationMs,
      xlsxGeneration: evidence.xlsxRender.durationMs,
      xlsxValidation: evidence.xlsxValidate.durationMs,
      websiteGeneration: evidence.websiteRender.durationMs,
      websiteValidation: evidence.websiteValidate.durationMs,
    },
  };
} finally {
  const cleanupStartedAt = performance.now();
  await fetch(`${baseUrl}/v1/sandbox/${sandboxId}`, {
    method: 'DELETE',
    headers,
  }).catch(() => undefined);
  if (report) {
    report.timingsMs.cleanup = Math.round(performance.now() - cleanupStartedAt);
    report.timingsMs.total = Math.round(performance.now() - smokeStartedAt);
  }
}

console.log(JSON.stringify(report, null, 2));
