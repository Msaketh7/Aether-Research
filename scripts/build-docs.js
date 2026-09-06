#!/usr/bin/env node
/*
 * build-docs.js — regenerate every Word (.docx) render of the design documents.
 *
 * Run after any edit to docs/PRD.md, docs/TDD.md, or a snapshot in
 * docs/versions/. One command keeps all .docx outputs in sync with their
 * Markdown sources.
 *
 *   cd scripts && npm run docs
 *   # or from repo root:
 *   node scripts/build-docs.js
 */

'use strict';

const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const scriptsDir = __dirname;
const repoRoot = path.resolve(scriptsDir, '..');
const docsDir = path.join(repoRoot, 'docs');
const versionsDir = path.join(docsDir, 'versions');
const converter = path.join(scriptsDir, 'md-to-docx.js');

function convert(mdPath, docxPath, title) {
  execFileSync(process.execPath, [converter, mdPath, docxPath, title], {
    stdio: 'inherit',
  });
}

// 1. masters
convert(path.join(docsDir, 'PRD.md'), path.join(docsDir, 'PRD.docx'),
  'Aether Research: PRD');
convert(path.join(docsDir, 'TDD.md'), path.join(docsDir, 'TDD.docx'),
  'Aether Research: TDD');

// 2. every versioned snapshot
if (fs.existsSync(versionsDir)) {
  const snaps = fs.readdirSync(versionsDir)
    .filter((f) => f.endsWith('.md'))
    .sort();
  for (const f of snaps) {
    const base = f.replace(/\.md$/, '');
    const m = base.match(/^(PRD|TDD)-v(.+)$/);
    const title = m
      ? `Aether Research: ${m[1]} v${m[2]}`
      : base;
    convert(path.join(versionsDir, f), path.join(versionsDir, base + '.docx'), title);
  }
}

console.log('\nAll .docx documents regenerated.');
