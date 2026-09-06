import { ExternalLink as ExternalLinkIcon } from 'lucide-react';
import { cn } from '@/lib/utils';

/**
 * Every outbound link to a research source. `noopener noreferrer` is not
 * optional here: these URLs come from search results, which are untrusted.
 */
export function SourceLink({
  href,
  children,
  className,
}: {
  href: string;
  children: React.ReactNode;
  className?: string;
}) {
  const isUpload = href.startsWith('upload://');

  if (isUpload) {
    return <span className={cn('text-foreground', className)}>{children}</span>;
  }

  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer nofollow"
      className={cn('inline-flex items-center gap-1 underline-offset-2 hover:underline', className)}
    >
      {children}
      <ExternalLinkIcon className="size-3 shrink-0 opacity-60" aria-hidden />
    </a>
  );
}
