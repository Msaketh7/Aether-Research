'use client';

import type { Citation, ReportSection } from '@aether/shared-types';
import { Fragment, type ReactNode } from 'react';
import { parseInline, parseMarkdown, type Block } from '@/lib/research/markdown';
import { cn } from '@/lib/utils';
import { CitationMarker } from './citation-marker';

/**
 * Renders a report section from parsed Markdown.
 *
 * Content is turned into React elements by the parser in lib/research/markdown
 * - never injected as HTML - because the text is synthesised from untrusted web
 * pages. Every `[n]` becomes an interactive citation resolved against the
 * citation list the API returned.
 */

function Inline({
  text,
  citationsByOrdinal,
}: {
  text: string;
  citationsByOrdinal: Map<number, Citation>;
}): ReactNode {
  return parseInline(text).map((token, index) => {
    switch (token.kind) {
      case 'bold':
        return (
          <strong key={index} className="font-semibold">
            {token.value}
          </strong>
        );
      case 'italic':
        return (
          <em key={index} className="italic">
            {token.value}
          </em>
        );
      case 'code':
        return (
          <code key={index} className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]">
            {token.value}
          </code>
        );
      case 'citation':
        return (
          <CitationMarker
            key={index}
            ordinal={token.ordinal}
            citation={citationsByOrdinal.get(token.ordinal)}
          />
        );
      default:
        return <Fragment key={index}>{token.value}</Fragment>;
    }
  });
}

function renderBlock(
  block: Block,
  index: number,
  citationsByOrdinal: Map<number, Citation>,
): ReactNode {
  const inline = (text: string) => <Inline text={text} citationsByOrdinal={citationsByOrdinal} />;

  switch (block.type) {
    case 'heading': {
      const Tag = (['h3', 'h4', 'h5'] as const)[block.level - 2] ?? 'h4';
      return (
        <Tag
          key={index}
          className={cn(
            'mt-6 font-semibold tracking-tight first:mt-0',
            block.level === 2 ? 'text-base' : 'text-sm',
          )}
        >
          {inline(block.text)}
        </Tag>
      );
    }

    case 'paragraph':
      return (
        <p key={index} className="mt-3 text-sm leading-relaxed first:mt-0">
          {inline(block.text)}
        </p>
      );

    case 'list': {
      const Tag = block.ordered ? 'ol' : 'ul';
      return (
        <Tag
          key={index}
          className={cn(
            'mt-3 flex flex-col gap-1.5 pl-5 text-sm leading-relaxed',
            block.ordered ? 'list-decimal' : 'list-disc',
          )}
        >
          {block.items.map((item, itemIndex) => (
            <li key={itemIndex} className="pl-1">
              {inline(item)}
            </li>
          ))}
        </Tag>
      );
    }

    case 'table':
      return (
        <div key={index} className="mt-4 w-full overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-border">
                {block.header.map((cell, cellIndex) => (
                  <th
                    key={cellIndex}
                    className="px-3 py-2 text-left text-xs font-medium text-muted-foreground"
                  >
                    {inline(cell)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, rowIndex) => (
                <tr key={rowIndex} className="border-b border-border/60 last:border-0">
                  {row.map((cell, cellIndex) => (
                    <td key={cellIndex} className="px-3 py-2 align-top leading-relaxed">
                      {inline(cell)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );

    default:
      return null;
  }
}

export function ReportSectionBody({
  section,
  citations,
}: {
  section: ReportSection;
  citations: Citation[];
}) {
  const citationsByOrdinal = new Map(citations.map((citation) => [citation.ordinal, citation]));
  const blocks = parseMarkdown(section.content_md);

  return (
    <div data-testid="report-section" data-kind={section.kind}>
      {blocks.map((block, index) => renderBlock(block, index, citationsByOrdinal))}
    </div>
  );
}
