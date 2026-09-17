/**
 * A deliberately small Markdown subset parser for report sections.
 *
 * Why not a Markdown library: report content is synthesised from *untrusted*
 * web pages. Rendering it through `dangerouslySetInnerHTML` would turn a
 * prompt-injection into an HTML-injection, so the renderer emits React elements
 * from a parsed token tree and can express nothing the parser does not
 * recognise. The supported subset is exactly what the synthesizer is prompted
 * to produce: headings, paragraphs, ordered and unordered lists, simple tables,
 * bold, italic, inline code and `[n]` citation markers.
 *
 * Pure functions, no React - unit-tested directly.
 */

export type InlineToken =
  | { kind: 'text'; value: string }
  | { kind: 'bold'; value: string }
  | { kind: 'italic'; value: string }
  | { kind: 'code'; value: string }
  | { kind: 'citation'; ordinal: number };

export type Block =
  | { type: 'heading'; level: 2 | 3 | 4; text: string }
  | { type: 'paragraph'; text: string }
  | { type: 'list'; ordered: boolean; items: string[] }
  | { type: 'table'; header: string[]; rows: string[][] };

/**
 * `\[` comes first so an escaped bracket is consumed before it can start a
 * citation marker. The API escapes verbatim text it embeds in a section - an
 * evidence quote, a source title - because a fetched page containing "[3]"
 * would otherwise render as a citation pointing at whatever source 3 is. That
 * is the one sequence in this subset that turns quoted text into a claim about
 * provenance, so it is the one that has an escape.
 */
const INLINE_PATTERN = /(\\\[|\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`|\[\d+\])/g;

/** Splits one line of text into inline tokens. Unmatched text passes through. */
export function parseInline(text: string): InlineToken[] {
  const tokens: InlineToken[] = [];
  let cursor = 0;

  for (const match of text.matchAll(INLINE_PATTERN)) {
    const index = match.index ?? 0;
    if (index > cursor) tokens.push({ kind: 'text', value: text.slice(cursor, index) });

    const raw = match[0];
    if (raw === '\\[') {
      tokens.push({ kind: 'text', value: '[' });
    } else if (raw.startsWith('**')) {
      tokens.push({ kind: 'bold', value: raw.slice(2, -2) });
    } else if (raw.startsWith('`')) {
      tokens.push({ kind: 'code', value: raw.slice(1, -1) });
    } else if (raw.startsWith('[')) {
      tokens.push({ kind: 'citation', ordinal: Number(raw.slice(1, -1)) });
    } else {
      tokens.push({ kind: 'italic', value: raw.slice(1, -1) });
    }
    cursor = index + raw.length;
  }

  if (cursor < text.length) tokens.push({ kind: 'text', value: text.slice(cursor) });
  return tokens;
}

function splitTableRow(line: string): string[] {
  return line
    .replace(/^\||\|$/g, '')
    .split('|')
    .map((cell) => cell.trim());
}

const HEADING = /^(#{2,4})\s+(.*)$/;
const UNORDERED = /^[-*]\s+(.*)$/;
const ORDERED = /^\d+[.)]\s+(.*)$/;
/** A wrapped list item: indented continuation of the previous bullet. */
const CONTINUATION = /^\s{2,}(\S.*)$/;

export function parseMarkdown(markdown: string): Block[] {
  const lines = markdown.replace(/\r\n/g, '\n').split('\n');
  const blocks: Block[] = [];

  let paragraph: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;

  const flushParagraph = () => {
    if (paragraph.length === 0) return;
    blocks.push({ type: 'paragraph', text: paragraph.join(' ').trim() });
    paragraph = [];
  };

  const flushList = () => {
    if (!list) return;
    blocks.push({ type: 'list', ordered: list.ordered, items: list.items });
    list = null;
  };

  const flushAll = () => {
    flushParagraph();
    flushList();
  };

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i] ?? '';
    const trimmed = line.trim();

    if (trimmed === '') {
      flushAll();
      continue;
    }

    const heading = HEADING.exec(trimmed);
    if (heading) {
      flushAll();
      const level = Math.min(4, Math.max(2, heading[1]?.length ?? 3)) as 2 | 3 | 4;
      blocks.push({ type: 'heading', level, text: heading[2] ?? '' });
      continue;
    }

    // A table needs a header row followed by a separator row of dashes.
    if (trimmed.startsWith('|') && (lines[i + 1] ?? '').trim().startsWith('|---')) {
      flushAll();
      const header = splitTableRow(trimmed);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && (lines[i] ?? '').trim().startsWith('|')) {
        rows.push(splitTableRow((lines[i] ?? '').trim()));
        i += 1;
      }
      i -= 1;
      blocks.push({ type: 'table', header, rows });
      continue;
    }

    const ordered = ORDERED.exec(trimmed);
    const unordered = UNORDERED.exec(trimmed);
    if (ordered || unordered) {
      flushParagraph();
      const isOrdered = ordered !== null;
      const item = (ordered?.[1] ?? unordered?.[1] ?? '').trim();
      if (list && list.ordered === isOrdered) {
        list.items.push(item);
      } else {
        flushList();
        list = { ordered: isOrdered, items: [item] };
      }
      continue;
    }

    // Indented text directly under a bullet continues that bullet.
    if (list && CONTINUATION.test(line)) {
      const last = list.items.length - 1;
      const existing = list.items[last];
      if (existing !== undefined) list.items[last] = `${existing} ${trimmed}`;
      continue;
    }

    flushList();
    paragraph.push(trimmed);
  }

  flushAll();
  return blocks;
}
