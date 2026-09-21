import Link from 'next/link';
import { APP_NAME } from '@/lib/api/config';
import { cn } from '@/lib/utils';

/**
 * The wordmark.
 *
 * The glyph is inline SVG rather than an icon-font character or an emoji: it
 * has to hold its stroke weight at 20px in the rail and at 28px on the sign-in
 * page, and it has to take its colour from the theme. Three orbits around a
 * point is the product - parallel researchers converging on one question.
 */
export function AetherMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      className={cn('size-5 text-primary', className)}
      aria-hidden
      focusable="false"
    >
      <circle cx="12" cy="12" r="2.6" fill="currentColor" />
      <ellipse
        cx="12"
        cy="12"
        rx="10"
        ry="4.4"
        stroke="currentColor"
        strokeWidth="1.5"
        opacity="0.9"
      />
      <ellipse
        cx="12"
        cy="12"
        rx="10"
        ry="4.4"
        stroke="currentColor"
        strokeWidth="1.5"
        opacity="0.55"
        transform="rotate(60 12 12)"
      />
      <ellipse
        cx="12"
        cy="12"
        rx="10"
        ry="4.4"
        stroke="currentColor"
        strokeWidth="1.5"
        opacity="0.35"
        transform="rotate(120 12 12)"
      />
    </svg>
  );
}

export function Brand({ href, className }: { href?: string; className?: string }) {
  const content = (
    <>
      <AetherMark className="size-5 shrink-0 transition-transform duration-[var(--duration-slow)] ease-[var(--ease-spring)] group-hover:rotate-[18deg]" />
      <span className="font-display truncate text-[0.95rem] font-semibold tracking-tight">
        {APP_NAME}
      </span>
    </>
  );

  if (!href) {
    return <span className={cn('group flex items-center gap-2', className)}>{content}</span>;
  }

  return (
    <Link href={href} className={cn('group flex items-center gap-2 rounded-md', className)}>
      {content}
    </Link>
  );
}
