'use client';

import { BadgeCheck, FileText, ShieldCheck, TriangleAlert } from 'lucide-react';
import Link from 'next/link';
import { ConfidenceMeter } from '@/components/common/confidence-meter';
import { EmptyState } from '@/components/common/empty-state';
import { ErrorState } from '@/components/common/error-state';
import { ReportSectionBody } from '@/components/research/report-view';
import { useRunContext } from '@/components/research/run-context';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { ApiError } from '@/lib/api/client';
import { NOT_MEASURED, formatCount, formatDateTime } from '@/lib/format';
import { useResearchReport } from '@/lib/api/queries';

/**
 * The final report (FR-9, FR-12).
 *
 * The citation-validation summary sits at the top of the page, before the prose.
 * How many citations were checked, and how many were rejected as unverifiable,
 * is context a reader needs before they start trusting the text.
 */
export default function RunReportPage() {
  const { runId, run } = useRunContext();
  const report = useResearchReport(runId);

  const notReady = report.error instanceof ApiError && report.error.isNotFound;

  if (report.isPending) {
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-24" />
        <Skeleton className="h-96" />
      </div>
    );
  }

  if (notReady) {
    const failed = run?.status === 'failed' || run?.status === 'cancelled';
    return (
      <EmptyState
        icon={FileText}
        title={failed ? 'No report was produced' : 'The report is not ready yet'}
        description={
          failed
            ? 'This run ended before synthesis. The sources and evidence it did gather are still available.'
            : 'A report is written after evidence extraction, verification and the critic loop, then every citation is validated before it is shown.'
        }
        action={
          <Button asChild variant="outline" size="sm">
            <Link href={`/research/${runId}/evidence`}>View evidence</Link>
          </Button>
        }
      />
    );
  }

  if (report.isError) {
    return <ErrorState error={report.error} onRetry={() => void report.refetch()} />;
  }

  const { report: meta, sections, citations, validation } = report.data;

  return (
    <div className="flex flex-col gap-5">
      <Card>
        <CardContent className="flex flex-wrap items-center gap-x-8 gap-y-4 pt-5">
          <div>
            <p className="text-xs text-muted-foreground">Overall confidence</p>
            {/* Null when the report cites no claim, so there was nothing to
                average. Shown as not computed rather than as a low score. */}
            {meta.overall_confidence === null ? (
              <p className="mt-1 font-mono text-sm text-muted-foreground">{NOT_MEASURED}</p>
            ) : (
              <ConfidenceMeter value={meta.overall_confidence} className="mt-1" />
            )}
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Citations</p>
            <p className="mt-1 font-mono text-sm tabular-nums">{formatCount(citations.length)}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Length</p>
            <p className="mt-1 font-mono text-sm tabular-nums">
              {formatCount(meta.word_count)} words
            </p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Synthesizer</p>
            <p className="mt-1 font-mono text-sm">{meta.model}</p>
          </div>
          <div className="ml-auto flex items-center gap-2">
            {meta.status === 'validated' ? (
              <Badge variant="success">
                <BadgeCheck className="size-3" aria-hidden />
                Citations validated
              </Badge>
            ) : (
              <Badge variant="warning">Draft, citations not yet validated</Badge>
            )}
          </div>
        </CardContent>
      </Card>

      {validation ? (
        <Alert variant={validation.rejected > 0 ? 'warning' : 'info'}>
          <ShieldCheck aria-hidden />
          <AlertDescription>
            <span className="font-medium text-foreground">
              {validation.valid} of {validation.checked} citations validated
            </span>{' '}
            on {formatDateTime(validation.validated_at)}.{' '}
            {validation.rejected > 0
              ? `${validation.rejected} could not be resolved and are shown in the text as unresolved rather than pointed at another source: ${validation.rejection_reasons
                  .map((reason) => `${reason.reason} (${reason.count})`)
                  .join('; ')}.`
              : 'Every citation resolves to an evidence span in a retrieved source.'}
          </AlertDescription>
        </Alert>
      ) : null}

      {meta.coverage_caveat ? (
        <Alert variant="warning">
          <TriangleAlert aria-hidden />
          <AlertDescription>{meta.coverage_caveat}</AlertDescription>
        </Alert>
      ) : null}

      {/*
       * On a wide screen the section list becomes a sticky rail. A synthesised
       * report runs to several screens, and a reader who wants Risks should not
       * have to scroll past Findings to discover that Risks exists.
       */}
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_13rem]">
        <article className="stagger flex min-w-0 flex-col gap-5">
          {sections.map((section) => (
            <Card key={section.id} id={section.kind} className="scroll-mt-32">
              <CardContent className="pt-5">
                <h2 className="text-base font-semibold tracking-tight">{section.heading}</h2>
                {/* `max-w-[68ch]` is the measure rule: past roughly 75
                    characters the eye loses the start of the next line. */}
                <div className="mt-3 max-w-[68ch]">
                  <ReportSectionBody section={section} citations={citations} />
                </div>
              </CardContent>
            </Card>
          ))}
        </article>

        <nav className="hidden xl:block" aria-label="Report sections">
          <div className="sticky top-32">
            <p className="px-3 pb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              Sections
            </p>
            <ul className="flex flex-col gap-0.5">
              {sections.map((section) => (
                <li key={section.id}>
                  <a
                    href={`#${section.kind}`}
                    className="block rounded-lg px-3 py-1.5 text-xs leading-snug text-muted-foreground transition-[color,background-color,transform] duration-[var(--duration-fast)] hover:translate-x-0.5 hover:bg-hover hover:text-foreground"
                  >
                    {section.heading}
                  </a>
                </li>
              ))}
            </ul>
          </div>
        </nav>
      </div>

      <p className="text-xs text-muted-foreground">
        Generated {formatDateTime(meta.generated_at)}. Every bracketed number resolves to a source
        and the exact quote it rests on. Click any bracketed number to see it.
      </p>
    </div>
  );
}
