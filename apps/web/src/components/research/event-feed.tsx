'use client';

import type { ResearchEvent } from '@aether/shared-types';
import { useEffect, useRef } from 'react';
import { SourceLink } from '@/components/common/external-link';
import { ScrollArea } from '@/components/ui/scroll-area';
import { describeEvent, type EventTone } from '@/lib/research/event-labels';
import { cn } from '@/lib/utils';

/**
 * The live event log.
 *
 * Auto-scroll follows the tail only while the user is already at the bottom;
 * yanking the viewport away from something the user is reading is a bug, not a
 * feature.
 */

const TONE_DOT: Record<EventTone, string> = {
  neutral: 'bg-muted-foreground/40',
  progress: 'bg-info',
  positive: 'bg-success',
  warning: 'bg-warning',
  danger: 'bg-destructive',
};

function timeOf(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? '--:--:--'
    : date.toLocaleTimeString('en-GB', { hour12: false });
}

export function EventFeed({
  events,
  emptyMessage = 'Waiting for the first event…',
  className,
  maxHeight = 'max-h-[26rem]',
}: {
  events: ResearchEvent[];
  emptyMessage?: string;
  className?: string;
  maxHeight?: string;
}) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const pinnedToBottom = useRef(true);

  useEffect(() => {
    const node = viewportRef.current;
    if (!node || !pinnedToBottom.current) return;
    node.scrollTop = node.scrollHeight;
  }, [events.length]);

  const onScroll = (event: React.UIEvent<HTMLDivElement>) => {
    const node = event.currentTarget;
    pinnedToBottom.current = node.scrollHeight - node.scrollTop - node.clientHeight < 40;
  };

  if (events.length === 0) {
    return (
      <p className={cn('px-2 py-8 text-center text-sm text-muted-foreground', className)}>
        {emptyMessage}
      </p>
    );
  }

  return (
    <ScrollArea className={cn(maxHeight, className)}>
      <div
        ref={viewportRef}
        onScroll={onScroll}
        className={cn('overflow-y-auto pr-2', maxHeight)}
        role="log"
        aria-live="polite"
        aria-label="Research activity"
      >
        <ol className="flex flex-col">
          {events.map((event) => {
            const described = describeEvent(event);
            return (
              <li
                key={event.seq}
                data-event-type={event.type}
                className="flex gap-3 border-b border-border/60 px-1 py-2 last:border-0"
              >
                <span className="pt-1.5">
                  <span
                    className={cn('block size-1.5 rounded-full', TONE_DOT[described.tone])}
                    aria-hidden
                  />
                </span>
                <span className="w-16 shrink-0 pt-0.5 font-mono text-[11px] tabular-nums text-muted-foreground">
                  {timeOf(event.at)}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-sm leading-snug">
                    {described.href ? (
                      <SourceLink href={described.href}>{described.title}</SourceLink>
                    ) : (
                      described.title
                    )}
                  </p>
                  {described.detail ? (
                    <p className="mt-0.5 truncate text-xs text-muted-foreground">
                      {described.detail}
                    </p>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ol>
      </div>
    </ScrollArea>
  );
}
