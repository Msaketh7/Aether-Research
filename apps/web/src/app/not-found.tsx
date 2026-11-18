import Link from 'next/link';
import { AppBackdrop } from '@/components/layout/app-backdrop';
import { Button } from '@/components/ui/button';

export default function NotFound() {
  return (
    <main className="flex min-h-dvh flex-col items-center justify-center gap-4 px-6 text-center">
      <AppBackdrop />
      <p className="font-mono text-xs uppercase tracking-widest text-muted-foreground">404</p>
      <h1 className="font-display text-[1.875rem] font-medium">That page does not exist</h1>
      <p className="max-w-md text-sm text-muted-foreground">
        The link may be stale, or the research run may have been removed.
      </p>
      <Button asChild className="mt-2">
        <Link href="/dashboard">Back to dashboard</Link>
      </Button>
    </main>
  );
}
