---
name: xlsx
description: "Builds real .xlsx workbooks inside the sandbox from a JSON spec - typed cells, a styled header row, column widths, number formats, freeze panes and autofilter - and verifies the result. Use when the requested deliverable is an Excel file: the request mentions Excel, xlsx, .xlsx, a spreadsheet, a workbook, \"tablo\", \"çalışma kitabı\", a budget, an inventory or a data export meant to be opened in Excel, Numbers, or Google Sheets. Also use to check whether an existing .xlsx is a valid workbook or has numbers stored as text. Do NOT use when a CSV is what was asked for - write the CSV directly instead; do NOT use for a PDF, a Markdown table, a chart image, or a database."
license: Apache-2.0
metadata:
  version: 1.1.0
---

# Excel workbook generation

## Overview

Two scripts. One builds a workbook from a JSON spec, the other proves the result is
a real workbook whose numbers are actually numbers.

- **`build_xlsx.py`** — JSON spec in, `.xlsx` out. Every column declares a type, and
  values are coerced to it.
- **`validate_xlsx.py`** — the exit gate. Confirms the file is a genuine workbook and
  reports the two defects that are invisible on screen: empty sheets, and numbers
  stored as text.

All script paths in this document are **relative to this skill's own directory**. If
the skill is materialised at `/workspace/.skills/xlsx/`, then `scripts/build_xlsx.py`
means `/workspace/.skills/xlsx/scripts/build_xlsx.py`. The scripts write only to the
output path they are given.

`openpyxl` and `pandas` are **already installed** in the sandbox image, so these
scripts need no setup step and tabular analysis needs no download. What else is
installed, and whether you may install more, are properties of the container and
the lease's policy rather than of this skill, and they change — call the
`workspace.env` tool rather than assuming either way.

### When this is the wrong tool

If the user asked for a **CSV**, write the CSV. It is a text file; producing one does
not need this skill, and handing back an `.xlsx` instead is a substitution the user
did not ask for. The reverse substitution is worse: never write a CSV and name it
`.xlsx`. Renaming does not convert, and `validate_xlsx.py` will reject it — as will
Excel.

## Quick start

```bash
cat > /workspace/spec.json <<'EOF'
{
  "properties": { "title": "Bölge Satışları" },
  "sheets": [
    {
      "name": "Satışlar",
      "columns": [
        { "header": "Bölge",  "key": "region", "type": "text",    "width": 22 },
        { "header": "Adet",   "key": "units",  "type": "integer", "format": "#,##0" },
        { "header": "Ciro",   "key": "gross",  "type": "number",  "format": "#,##0.00 ₺" },
        { "header": "Değişim","key": "change", "type": "number",  "format": "0.0%" },
        { "header": "Tarih",  "key": "date",   "type": "date" }
      ],
      "rows": [
        { "region": "İstanbul", "units": 4210, "gross": 918450.5, "change": 0.18,  "date": "2026-07-31" },
        { "region": "İzmir",    "units": 1904, "gross": 402100.0, "change": 0.0,   "date": "2026-07-31" },
        { "region": "Şanlıurfa","units": 806,  "gross": 151900.25,"change": -0.04, "date": "2026-07-31" }
      ]
    }
  ]
}
EOF

python3 scripts/build_xlsx.py /workspace/spec.json /workspace/satislar.xlsx
python3 scripts/validate_xlsx.py /workspace/satislar.xlsx
```

Expected output from the second command:

```
OK: /workspace/satislar.xlsx is an .xlsx workbook, 5729 bytes, 1 sheet(s)
  Satışlar: 4 row(s), 20 populated cell(s)
```

If validation prints `ERROR:` lines, the file is not deliverable. Fix the cause and
re-run — do not describe a failed build as a finished workbook.

## Task: build a workbook

```bash
python3 scripts/build_xlsx.py <spec.json> <output.xlsx> [--validate-only]
```

Run `--validate-only` first when the spec is generated programmatically: it checks
the entire spec and exits without writing anything, so a malformed spec costs one
fast run instead of a broken file.

### Spec structure

| Field        | Required | Notes                                                |
|--------------|----------|------------------------------------------------------|
| `sheets`     | yes      | At least one sheet. Order is preserved.              |
| `properties` | no       | `title`, `creator`, `subject`, `description`.        |
| `font`       | no       | Explicit safe family override; defaults to Chainabit `IBM Plex Sans`. |

Each sheet:

| Field          | Required | Notes                                                       |
|----------------|----------|-------------------------------------------------------------|
| `name`         | yes      | 1–31 characters, none of `\ / * ? : [ ]`. Must be unique.   |
| `columns`      | yes      | At least one. Defines the header row and the cell types.    |
| `rows`         | yes      | May be empty, but the key must be present.                  |
| `freezeHeader` | no       | `true` by default — header stays visible while scrolling.   |
| `autoFilter`   | no       | `true` by default — adds the filter dropdowns to the header. |

Each column:

| Field    | Required | Notes                                                          |
|----------|----------|-----------------------------------------------------------------|
| `header` | yes      | The text in row 1.                                              |
| `type`   | no       | Defaults to `text`. See the type table below.                   |
| `key`    | no       | Required only when rows are given as objects.                   |
| `width`  | no       | Column width in characters, 1–255. Auto-sized from content otherwise. |
| `format` | no       | Excel number-format string, e.g. `#,##0.00`, `0.0%`, `yyyy-mm-dd`. |

### Column types

| `type`     | Accepts                                    | Stored as                          |
|------------|--------------------------------------------|-------------------------------------|
| `text`     | any scalar                                  | string                              |
| `number`   | number, or a numeric string                 | float, format `General` by default  |
| `integer`  | whole number, or a whole-number string      | int, format `0` by default          |
| `date`     | `"YYYY-MM-DD"`                              | date, format `yyyy-mm-dd` by default |
| `datetime` | `"YYYY-MM-DDTHH:MM:SS"`                     | datetime, format `yyyy-mm-dd hh:mm`  |
| `boolean`  | `true` / `false`                            | boolean                             |
| `formula`  | string starting with `=`                    | formula, evaluated when opened      |

`null` is accepted by every type and produces an empty cell.

Percentages: store the **fraction** and let the format do the display. `0.18` with
format `0.0%` shows as `18.0%` and still sums correctly. Storing `18` with a percent
format shows `1800.0%`, and storing `"18%"` in a text column makes the value dead
weight.

Timezones are dropped from `datetime` values. Excel has no timezone concept, so
writing one would mean the reader silently reinterprets it in an unrelated zone.

### Rows

Two equivalent forms, chosen per row:

```json
{ "region": "İzmir", "units": 1904 }
```

Object form, looked up by each column's `key`. Every column must declare a `key`.
Readable and order-independent; prefer it.

```json
["İzmir", 1904]
```

Array form, positional. Cell count must equal column count exactly.

### Error output

Validation reports **every** problem in one run, addressed by path:

```
ERROR: sheets[0].name: must not contain any of \ / * ? : [ ]
ERROR: sheets[0].columns[2].type: must be one of text, number, integer, date, datetime, boolean, formula, found 'currency'
ERROR: sheets[0].rows[4]: has 4 cells but 5 columns are declared
ERROR: sheets[0].rows[7][2]: 'n/a' is not a number. A number column stores numbers; use a text column if the value is genuinely a label.
```

Correct all of them at once and re-run. Do not fix one and retry.

That last error is the one worth reading carefully. When a data set has a `"n/a"` or
`"—"` mixed into an otherwise numeric column, the right fix is almost always `null`
for those cells, not switching the whole column to `text` — switching kills sorting
and summing for every row that was fine.

## Task: validate a workbook

```bash
python3 scripts/validate_xlsx.py <file.xlsx> [--strict]
```

Confirms the file is a ZIP container holding the required workbook parts, lists every
sheet with its row and populated-cell counts, and warns about:

- a sheet that is completely empty, or has a header row and no data;
- cells holding a number stored as text, which Excel will not sort, sum, or chart.

Exit codes:

- `0` — the workbook is usable. `WARNING:` lines may still appear; read them before
  delivering.
- `1` — do not hand the file to the user. Missing, empty, not a ZIP, missing workbook
  parts, corrupt, no sheets, or every sheet empty.

`--strict` promotes warnings to failures. Use it when the workbook is meant to be
fully populated.

The check is stdlib-only and reads the file's own structure, so it works on workbooks
this skill did not produce.

## Working pattern

1. **Plan.** Decide the columns and their types before writing any data. The type is
   the decision that matters; everything else is cosmetic.
2. **Validate.** Write the spec, run `--validate-only`, fix every `ERROR:` line.
3. **Execute.** Build the workbook.
4. **Verify.** Run `validate_xlsx.py`. Only after it exits 0 is there a file to talk
   about.

## Script quick reference

| Task                                   | Script                      | Command |
|----------------------------------------|-----------------------------|---------|
| Build a workbook from a spec            | `scripts/build_xlsx.py`     | `python3 scripts/build_xlsx.py spec.json out.xlsx` |
| Check a spec before building            | `scripts/build_xlsx.py`     | `python3 scripts/build_xlsx.py spec.json out.xlsx --validate-only` |
| Confirm a workbook is real and populated | `scripts/validate_xlsx.py`  | `python3 scripts/validate_xlsx.py out.xlsx` |
| Same, failing on any warning            | `scripts/validate_xlsx.py`  | `python3 scripts/validate_xlsx.py out.xlsx --strict` |
| Inspect a workbook produced elsewhere   | `scripts/validate_xlsx.py`  | `python3 scripts/validate_xlsx.py received.xlsx` |

Both scripts accept `--help`.
