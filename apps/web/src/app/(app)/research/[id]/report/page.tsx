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
import { formatCount, formatDateTime } from '@/lib/format';
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
        <CardContent className="flex flex-wrap items-center gap-x-6 gap-y-3 pt-5">
          <div>
            <p className="text-xs text-muted-foreground">Overall confidence</p>
            <ConfidenceMeter value={meta.overall_confidence} className="mt-1" />
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
              <Badge variant="warning">Draft — citations not yet validated</Badge>
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
              ? `${validation.rejected} were rejected and removed from the report: ${validation.rejection_reasons
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

      <article className="flex flex-col gap-5">
        {sections.map((section) => (
          <Card key={section.id} id={section.kind}>
            <CardContent className="pt-5">
              <h2 className="text-base font-semibold tracking-tight">{section.heading}</h2>
              <div className="mt-3">
                <ReportSectionBody section={section} citations={citations} />
              </div>
            </CardContent>
          </Card>
        ))}
      </article>

      <p className="text-xs text-muted-foreground">
        Generated {formatDateTime(meta.generated_at)}. Every bracketed number resolves to a source
        and the exact quote it rests on — click one to see it.
      </p>
    </div>
  );
}
