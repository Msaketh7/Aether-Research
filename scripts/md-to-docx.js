#!/usr/bin/env node
/*
 * md-to-docx.js — minimal, dependency-light Markdown → Word (.docx) converter
 * for the Aether Research design documents (PRD, TDD, and their versioned
 * snapshots).
 *
 * Supports the subset of Markdown used in these docs:
 *   - ATX headings (#..######)
 *   - paragraphs with inline **bold**, *italic*, `code`, [text](url)
 *   - bullet lists (-, *) and ordered lists (1.), one level of nesting via indent
 *   - blockquotes (>)
 *   - fenced code blocks (``` )
 *   - pipe tables with a --- separator row
 *   - horizontal rules (--- / *** on their own line)
 *
 * Usage:
 *   node scripts/md-to-docx.js <input.md> <output.docx> ["Optional Doc Title"]
 *
 * The `docx` npm package must be resolvable. If it is not installed in this
 * repo, the script falls back to the copy installed in the session scratchpad.
 */

'use strict';

const fs = require('fs');
const path = require('path');

// ---- resolve the `docx` package -------------------------------------------------
let docx;
try {
  docx = require('docx');
} catch (e) {
  const fallbacks = [
    process.env.DOCX_LIB_DIR,
    path.join(process.env.LOCALAPPDATA || '', 'Temp/claude'),
  ].filter(Boolean);
  // last resort: let the caller pass NODE_PATH
  try {
    docx = require(require.resolve('docx', { paths: fallbacks }));
  } catch (e2) {
    console.error(
      'Cannot find the "docx" package. Install it (npm i docx) or set ' +
        'NODE_PATH to a node_modules dir that contains it.',
    );
    process.exit(1);
  }
}

const {
  Document,
  Packer,
  Paragraph,
  TextRun,
  ExternalHyperlink,
  HeadingLevel,
  AlignmentType,
  Table,
  TableRow,
  TableCell,
  WidthType,
  BorderStyle,
  ShadingType,
  PageOrientation,
  LevelFormat,
} = docx;

// ---- layout constants ---------------------------------------------------------
const PAGE_WIDTH = 12240; // US Letter, DXA (1440 = 1 inch)
const PAGE_HEIGHT = 15840;
const MARGIN = 1440;
const CONTENT_WIDTH = PAGE_WIDTH - MARGIN * 2; // 9360

const MONO = 'Consolas';
const BODY = 'Calibri';

const HEADING_BY_LEVEL = {
  1: HeadingLevel.HEADING_1,
  2: HeadingLevel.HEADING_2,
  3: HeadingLevel.HEADING_3,
  4: HeadingLevel.HEADING_4,
  5: HeadingLevel.HEADING_5,
  6: HeadingLevel.HEADING_6,
};

// ---- inline parsing ---------------------------------------------------------
// Splits a line into TextRun / ExternalHyperlink children, honouring
// **bold**, *italic* / _italic_, `code`, and [label](url).
function parseInline(text, baseOpts = {}) {
  const runs = [];
  let i = 0;
  const pushText = (s, opts) => {
    if (!s) return;
    runs.push(new TextRun({ text: s, font: BODY, ...baseOpts, ...opts }));
  };

  const patterns = [
    { re: /^\*\*([^*]+)\*\*/, opts: { bold: true } },
    { re: /^__([^_]+)__/, opts: { bold: true } },
    { re: /^\*([^*]+)\*/, opts: { italics: true } },
    { re: /^`([^`]+)`/, opts: { font: MONO } },
  ];

  let buf = '';
  while (i < text.length) {
    const rest = text.slice(i);

    // link
    const link = rest.match(/^\[([^\]]+)\]\(([^)]+)\)/);
    if (link) {
      pushText(buf, {});
      buf = '';
      const label = link[1];
      const url = link[2];
      if (/^https?:\/\//i.test(url)) {
        runs.push(
          new ExternalHyperlink({
            link: url,
            children: [new TextRun({ text: label, font: BODY, style: 'Hyperlink' })],
          }),
        );
      } else {
        // internal/relative link — render label, keep the path in parens muted
        pushText(label, {});
      }
      i += link[0].length;
      continue;
    }

    let matched = false;
    for (const p of patterns) {
      const m = rest.match(p.re);
      if (m) {
        pushText(buf, {});
        buf = '';
        pushText(m[1], p.opts);
        i += m[0].length;
        matched = true;
        break;
      }
    }
    if (matched) continue;

    buf += text[i];
    i += 1;
  }
  pushText(buf, {});
  if (runs.length === 0) pushText(' ', {});
  return runs;
}

// ---- table parsing ---------------------------------------------------------
function isTableSeparator(line) {
  return /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
}
function splitRow(line) {
  let s = line.trim();
  if (s.startsWith('|')) s = s.slice(1);
  if (s.endsWith('|')) s = s.slice(0, -1);
  // split on unescaped pipes
  return s.split(/\s*(?<!\\)\|\s*/).map((c) => c.replace(/\\\|/g, '|').trim());
}

function buildTable(headerCells, bodyRows) {
  const cols = headerCells.length;
  // A `| | |` metadata table has no real headings — render it without a
  // styled/shaded header row.
  const headerless = headerCells.every((c) => c.trim() === '');

  // proportional column widths from content length, clamped
  const colTextLen = new Array(cols).fill(0);
  const consider = (cells) =>
    cells.forEach((c, idx) => {
      if (idx < cols) colTextLen[idx] = Math.max(colTextLen[idx], c.length);
    });
  consider(headerCells);
  bodyRows.forEach(consider);
  const totalLen = colTextLen.reduce((a, b) => a + b, 0) || cols;
  const minW = Math.max(900, Math.floor(CONTENT_WIDTH / (cols * 3)));
  let widths = colTextLen.map((l) => Math.max(minW, Math.round((l / totalLen) * CONTENT_WIDTH)));
  // normalise to exactly CONTENT_WIDTH
  let sum = widths.reduce((a, b) => a + b, 0);
  widths[cols - 1] += CONTENT_WIDTH - sum;
  if (widths[cols - 1] < minW) {
    widths = new Array(cols).fill(Math.floor(CONTENT_WIDTH / cols));
    widths[cols - 1] += CONTENT_WIDTH - widths.reduce((a, b) => a + b, 0);
  }

  const border = { style: BorderStyle.SINGLE, size: 4, color: 'B0B0B0' };
  const borders = {
    top: border,
    bottom: border,
    left: border,
    right: border,
    insideHorizontal: border,
    insideVertical: border,
  };

  const makeCell = (text, idx, opts = {}) =>
    new TableCell({
      width: { size: widths[idx], type: WidthType.DXA },
      shading: opts.header ? { type: ShadingType.CLEAR, fill: 'E8E8E8', color: 'auto' } : undefined,
      margins: { top: 60, bottom: 60, left: 100, right: 100 },
      children: [
        new Paragraph({
          spacing: { before: 0, after: 0 },
          children: parseInline(text, opts.header ? { bold: true } : {}),
        }),
      ],
    });

  const rows = [];
  if (!headerless) {
    rows.push(
      new TableRow({
        tableHeader: true,
        children: headerCells.map((c, idx) => makeCell(c, idx, { header: true })),
      }),
    );
  }
  for (const r of bodyRows) {
    const cells = [];
    for (let idx = 0; idx < cols; idx++) {
      cells.push(makeCell(r[idx] != null ? r[idx] : '', idx));
    }
    rows.push(new TableRow({ children: cells }));
  }

  return new Table({
    columnWidths: widths,
    width: { size: CONTENT_WIDTH, type: WidthType.DXA },
    borders,
    rows,
  });
}

// ---- main block parser ---------------------------------------------------------
function parseMarkdown(md) {
  const lines = md.replace(/\r\n/g, '\n').split('\n');
  const out = [];
  let i = 0;

  while (i < lines.length) {
    let line = lines[i];

    // blank
    if (/^\s*$/.test(line)) {
      i += 1;
      continue;
    }

    // fenced code block
    const fence = line.match(/^\s*```/);
    if (fence) {
      i += 1;
      const code = [];
      while (i < lines.length && !/^\s*```/.test(lines[i])) {
        code.push(lines[i]);
        i += 1;
      }
      i += 1; // closing fence
      const shading = { type: ShadingType.CLEAR, fill: 'F4F4F4', color: 'auto' };
      if (code.length === 0) code.push('');
      code.forEach((c, idx) => {
        out.push(
          new Paragraph({
            shading,
            spacing: {
              before: idx === 0 ? 120 : 0,
              after: idx === code.length - 1 ? 120 : 0,
              line: 264,
            },
            border:
              idx === 0
                ? { top: { style: BorderStyle.SINGLE, size: 4, color: 'DDDDDD' } }
                : undefined,
            children: [new TextRun({ text: c || ' ', font: MONO, size: 18 })],
          }),
        );
      });
      continue;
    }

    // horizontal rule
    if (/^\s*([-*_])\1{2,}\s*$/.test(line)) {
      out.push(
        new Paragraph({
          spacing: { before: 120, after: 120 },
          border: {
            bottom: { style: BorderStyle.SINGLE, size: 6, color: '999999' },
          },
          children: [new TextRun({ text: '' })],
        }),
      );
      i += 1;
      continue;
    }

    // heading
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      const level = h[1].length;
      out.push(
        new Paragraph({
          heading: HEADING_BY_LEVEL[level],
          spacing: { before: level <= 2 ? 240 : 160, after: 100 },
          children: parseInline(h[2].replace(/\s+#*\s*$/, '')),
        }),
      );
      i += 1;
      continue;
    }

    // table
    if (line.includes('|') && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
      const header = splitRow(line);
      i += 2;
      const body = [];
      while (i < lines.length && lines[i].includes('|') && !/^\s*$/.test(lines[i])) {
        if (isTableSeparator(lines[i])) {
          i += 1;
          continue;
        }
        body.push(splitRow(lines[i]));
        i += 1;
      }
      out.push(buildTable(header, body));
      out.push(new Paragraph({ spacing: { after: 80 }, children: [new TextRun('')] }));
      continue;
    }

    // blockquote (may span multiple lines)
    if (/^\s*>\s?/.test(line)) {
      const quote = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        quote.push(lines[i].replace(/^\s*>\s?/, ''));
        i += 1;
      }
      const text = quote.join(' ').replace(/\s+/g, ' ').trim();
      out.push(
        new Paragraph({
          spacing: { before: 120, after: 120 },
          indent: { left: 360 },
          border: {
            left: { style: BorderStyle.SINGLE, size: 18, color: 'BBBBBB', space: 12 },
          },
          shading: { type: ShadingType.CLEAR, fill: 'F7F7F7', color: 'auto' },
          children: parseInline(text, { italics: true }),
        }),
      );
      continue;
    }

    // list (bullet or ordered), one nesting level via leading spaces
    const listItem = line.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
    if (listItem) {
      while (i < lines.length) {
        const m = lines[i].match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
        if (!m) {
          // allow a wrapped continuation line (indented, no marker)
          if (/^\s+\S/.test(lines[i]) && !/^\s*$/.test(lines[i]) && out.length) {
            // append to previous paragraph
            const prev = out[out.length - 1];
            if (prev instanceof Paragraph) {
              prev.addChildElement(new TextRun({ text: ' ' + lines[i].trim(), font: BODY }));
            }
            i += 1;
            continue;
          }
          break;
        }
        const indent = m[1].replace(/\t/g, '    ').length;
        const ordered = /\d/.test(m[2]);
        const level = indent >= 2 ? 1 : 0;
        out.push(
          new Paragraph({
            numbering: ordered ? { reference: 'ordered', level } : undefined,
            bullet: ordered ? undefined : { level },
            spacing: { before: 20, after: 20 },
            children: parseInline(m[3]),
          }),
        );
        i += 1;
      }
      continue;
    }

    // plain paragraph (gather consecutive non-blank, non-special lines)
    const para = [line];
    i += 1;
    while (
      i < lines.length &&
      !/^\s*$/.test(lines[i]) &&
      !/^(#{1,6})\s/.test(lines[i]) &&
      !/^\s*```/.test(lines[i]) &&
      !/^\s*>\s?/.test(lines[i]) &&
      !/^(\s*)([-*+]|\d+[.)])\s+/.test(lines[i]) &&
      !(lines[i].includes('|') && i + 1 < lines.length && isTableSeparator(lines[i + 1])) &&
      !/^\s*([-*_])\1{2,}\s*$/.test(lines[i])
    ) {
      para.push(lines[i]);
      i += 1;
    }
    out.push(
      new Paragraph({
        spacing: { before: 40, after: 80, line: 276 },
        children: parseInline(para.join(' ')),
      }),
    );
  }

  return out;
}

// ---- driver ---------------------------------------------------------------
function main() {
  const [, , inPath, outPath, titleArg] = process.argv;
  if (!inPath || !outPath) {
    console.error('Usage: node scripts/md-to-docx.js <input.md> <output.docx> ["Title"]');
    process.exit(1);
  }
  const md = fs.readFileSync(inPath, 'utf8');
  const children = parseMarkdown(md);

  const doc = new Document({
    creator: 'Aether Research',
    title: titleArg || path.basename(inPath, '.md'),
    styles: {
      default: {
        document: { run: { font: BODY, size: 21 } },
      },
    },
    numbering: {
      config: [
        {
          reference: 'ordered',
          levels: [
            {
              level: 0,
              format: LevelFormat.DECIMAL,
              text: '%1.',
              alignment: AlignmentType.START,
              style: { paragraph: { indent: { left: 460, hanging: 320 } } },
            },
            {
              level: 1,
              format: LevelFormat.LOWER_LETTER,
              text: '%2.',
              alignment: AlignmentType.START,
              style: { paragraph: { indent: { left: 920, hanging: 320 } } },
            },
          ],
        },
      ],
    },
    sections: [
      {
        properties: {
          page: {
            size: { width: PAGE_WIDTH, height: PAGE_HEIGHT, orientation: PageOrientation.PORTRAIT },
            margin: { top: MARGIN, bottom: MARGIN, left: MARGIN, right: MARGIN },
          },
        },
        children,
      },
    ],
  });

  Packer.toBuffer(doc).then((buf) => {
    fs.writeFileSync(outPath, buf);
    console.log(
      `wrote ${outPath} (${buf.length.toLocaleString()} bytes, ${children.length} blocks)`,
    );
  });
}

main();
