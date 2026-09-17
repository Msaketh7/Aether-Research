import { describe, expect, it } from 'vitest';
import { parseInline, parseMarkdown } from './markdown';

/**
 * The parser is a security boundary as well as a formatter: report text is
 * synthesised from untrusted web pages, and anything the parser does not
 * recognise must survive as literal text rather than becoming markup.
 */
describe('parseInline', () => {
  it('extracts citation ordinals', () => {
    const tokens = parseInline('Revenue grew [3] last year [12].');
    expect(tokens.filter((token) => token.kind === 'citation')).toEqual([
      { kind: 'citation', ordinal: 3 },
      { kind: 'citation', ordinal: 12 },
    ]);
  });

  it('handles bold, italic and inline code', () => {
    const tokens = parseInline('**bold** and *italic* and `code`');
    expect(tokens.map((token) => token.kind)).toEqual(['bold', 'text', 'italic', 'text', 'code']);
  });

  it('treats HTML as literal text', () => {
    const tokens = parseInline('<script>alert(1)</script>');
    expect(tokens).toEqual([{ kind: 'text', value: '<script>alert(1)</script>' }]);
  });

  it('leaves an unmatched bracket alone', () => {
    expect(parseInline('an [unclosed marker')).toEqual([
      { kind: 'text', value: 'an [unclosed marker' },
    ]);
  });

  it('renders an escaped bracket as text rather than a citation', () => {
    // The API escapes verbatim text it embeds in a section, because a quoted
    // page containing "[3]" would otherwise be shown to the reader as a
    // citation to whichever source is third.
    // Exactly what the API emits: only the opening bracket is escaped, which is
    // enough to stop the marker matching.
    const tokens = parseInline('the filing said \\[3] were pending');
    expect(tokens.some((token) => token.kind === 'citation')).toBe(false);
    expect(tokens.map((token) => (token.kind === 'text' ? token.value : '')).join('')).toBe(
      'the filing said [3] were pending',
    );
  });
});

describe('parseMarkdown', () => {
  it('joins wrapped lines into one paragraph', () => {
    const blocks = parseMarkdown('The market is\ngrowing quickly.');
    expect(blocks).toEqual([{ type: 'paragraph', text: 'The market is growing quickly.' }]);
  });

  it('parses headings at the levels the synthesizer emits', () => {
    const blocks = parseMarkdown('### Market\n\nText.\n\n#### Detail');
    expect(blocks[0]).toEqual({ type: 'heading', level: 3, text: 'Market' });
    expect(blocks[2]).toEqual({ type: 'heading', level: 4, text: 'Detail' });
  });

  it('parses ordered and unordered lists separately', () => {
    const blocks = parseMarkdown('1. first\n2. second\n\n- alpha\n- beta');
    expect(blocks).toEqual([
      { type: 'list', ordered: true, items: ['first', 'second'] },
      { type: 'list', ordered: false, items: ['alpha', 'beta'] },
    ]);
  });

  it('folds an indented continuation into the preceding list item', () => {
    const blocks = parseMarkdown('1. first line\n   continued here\n2. second');
    expect(blocks).toEqual([
      { type: 'list', ordered: true, items: ['first line continued here', 'second'] },
    ]);
  });

  it('parses a table with a separator row', () => {
    const blocks = parseMarkdown('| Axis | Leader |\n|---|---|\n| Latency | Custom silicon |');
    expect(blocks).toEqual([
      {
        type: 'table',
        header: ['Axis', 'Leader'],
        rows: [['Latency', 'Custom silicon']],
      },
    ]);
  });

  it('does not treat a pipe-containing paragraph as a table', () => {
    const blocks = parseMarkdown('Cost | benefit trade-offs matter.');
    expect(blocks[0]?.type).toBe('paragraph');
  });

  it('returns nothing for empty content', () => {
    expect(parseMarkdown('')).toEqual([]);
    expect(parseMarkdown('\n\n  \n')).toEqual([]);
  });
});
