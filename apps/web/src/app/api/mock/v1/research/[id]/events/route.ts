import { getEntry } from '@/mocks/store';
import { notFound } from '../../../_lib/respond';

export const dynamic = 'force-dynamic';

/**
 * Mock Server-Sent Events endpoint (ADR 0006).
 *
 * Deliberately faithful to the contract the real API implements (Phase 14):
 *
 * - every frame carries `id:` (the sequence number), so the browser's native
 *   `EventSource` sends `Last-Event-ID` on reconnect and this handler replays
 *   from there instead of duplicating the whole run;
 * - each event is a named SSE event, so the client subscribes per type;
 * - a comment heartbeat keeps intermediaries from closing an idle stream;
 * - the stream closes once a terminal event has been written.
 *
 * Events are released at their scheduled offset, so the stream and the REST
 * endpoints - which project the same timeline onto the same clock - always
 * agree about what has happened.
 */

const HEARTBEAT_MS = 15_000;

function speed(): number {
  const raw = Number(process.env.NEXT_PUBLIC_MOCK_SPEED ?? '1');
  return Number.isFinite(raw) && raw >= 1 ? raw : 1;
}

export async function GET(request: Request, context: { params: Promise<{ id: string }> }) {
  const { id } = await context.params;
  const entry = getEntry(id);
  if (!entry) return notFound('run');

  const lastEventId = Number(request.headers.get('last-event-id') ?? '0');
  const resumeFrom = Number.isFinite(lastEventId) ? lastEventId : 0;
  const factor = speed();
  const encoder = new TextEncoder();

  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      let closed = false;

      const write = (chunk: string) => {
        if (closed) return;
        try {
          controller.enqueue(encoder.encode(chunk));
        } catch {
          close();
        }
      };

      // Comment heartbeat, so proxies do not close an idle stream.
      const heartbeat = setInterval(() => write(`: heartbeat\n\n`), HEARTBEAT_MS / factor);

      function close() {
        if (closed) return;
        closed = true;
        clearInterval(heartbeat);
        try {
          controller.close();
        } catch {
          // Already closed by the client disconnecting.
        }
      }

      request.signal.addEventListener('abort', close);

      // Retry hint for the browser's own reconnection logic.
      write(`retry: 3000\n\n`);

      const pending = entry.timeline.filter((item) => item.event.seq > resumeFrom);

      for (const item of pending) {
        if (closed) break;

        // Real time at which this event becomes due, honouring the speed-up.
        const dueAt = entry.startedAtMs + item.offsetMs / factor;
        const wait = dueAt - Date.now();
        if (wait > 0) {
          await new Promise((resolve) => setTimeout(resolve, wait));
          if (closed) break;
        }

        // A cancellation mid-stream stops the run where it stands.
        if (entry.cancelledAtMs !== null && item.event.type !== 'research_cancelled') {
          const cancelled = {
            seq: item.event.seq,
            type: 'research_cancelled' as const,
            run_id: entry.dataset.spec.id,
            at: new Date(entry.cancelledAtMs).toISOString(),
            status: 'cancelled' as const,
            payload: { cancelled_by: 'user' },
          };
          write(
            `id: ${cancelled.seq}\nevent: research_cancelled\ndata: ${JSON.stringify(cancelled)}\n\n`,
          );
          close();
          return;
        }

        write(
          `id: ${item.event.seq}\nevent: ${item.event.type}\ndata: ${JSON.stringify(item.event)}\n\n`,
        );

        if (
          item.event.type === 'report_completed' ||
          item.event.type === 'research_failed' ||
          item.event.type === 'research_cancelled'
        ) {
          close();
          return;
        }
      }

      close();
    },
  });

  return new Response(stream, {
    headers: {
      'Content-Type': 'text/event-stream; charset=utf-8',
      'Cache-Control': 'no-cache, no-transform',
      Connection: 'keep-alive',
      // Nginx and friends buffer streamed responses unless told not to.
      'X-Accel-Buffering': 'no',
    },
  });
}
