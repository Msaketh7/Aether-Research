import { Network, Quote, Scale, type LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { Brand } from '@/components/layout/brand';
import { AuthBackdrop } from './auth-backdrop';
import { PipelineTicker } from './pipeline-ticker';

const HEADLINE = ['Research', 'that', 'shows'];
const HEADLINE_ACCENT = ['its', 'work.'];

const FEATURES: { icon: LucideIcon; title: string; body: string }[] = [
  {
    icon: Network,
    title: 'Parallel researchers',
    body: 'Your question is split into parts, and every part is investigated at once.',
  },
  {
    icon: Quote,
    title: 'Cited to the sentence',
    body: 'Each claim in the report points at the verbatim span it rests on.',
  },
  {
    icon: Scale,
    title: 'Contradictions surfaced',
    body: 'Where sources disagree you see both sides, not a smoothed-over average.',
  },
];

/**
 * The stage both credential pages stand on.
 *
 * The stage the rest of the product is styled after: the same palette, glass
 * and light as every signed-in page, with the light here moving - the video,
 * and a parallax over it - where the app's is painted and still.
 *
 * Two columns from `lg`: the case for the product on the left, the form on
 * the right. Below that the story is dropped rather than stacked - on a phone
 * the person came to sign in, and three paragraphs above the email field would
 * push it under the keyboard.
 *
 * The pointer's parallax over the light belongs to the backdrop, which owns
 * the layer it moves.
 */
export function AuthShell({ children }: { children: ReactNode }) {
  return (
    <main className="auth-stage relative min-h-dvh overflow-x-clip text-foreground">
      <AuthBackdrop />

      <div className="relative z-10 mx-auto grid min-h-dvh w-full max-w-7xl grid-cols-1 items-center gap-12 px-4 py-10 sm:px-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,27rem)] lg:gap-20 lg:px-12 xl:gap-28 [@media(max-height:50rem)]:py-6">
        <Story />

        <div className="mx-auto w-full max-w-[27rem] lg:mx-0">
          {/* The shadow is for the bright band this sits on at phone width. */}
          <div className="enter mb-8 flex justify-center drop-shadow-[0_1px_10px_oklch(0.09_0.02_266/0.9)] lg:hidden">
            <Brand className="text-foreground [&_svg]:size-7 [&>span:last-child]:text-lg" />
          </div>
          {children}
        </div>
      </div>
    </main>
  );
}

function Story() {
  return (
    <section
      aria-label="About Aether Research"
      className="hidden flex-col gap-10 lg:flex [@media(max-height:50rem)]:gap-7"
    >
      <Brand className="enter w-fit text-foreground [&_svg]:size-7 [&>span:last-child]:text-lg" />

      <div className="flex flex-col gap-6">
        {/* Each word is its own span so it can arrive on its own beat. The
            sentence is still one string to a screen reader - the spans carry
            no semantics and the spaces between them are real text. */}
        <p className="font-display max-w-xl text-[clamp(2.75rem,4.6vw,4.25rem)] leading-[1.02] font-medium text-balance text-foreground">
          {HEADLINE.map((word, index) => (
            <span key={word}>
              <span
                className="enter-word"
                style={{ ['--enter-delay' as string]: `${140 + index * 70}ms` }}
              >
                {word}
              </span>{' '}
            </span>
          ))}
          {HEADLINE_ACCENT.map((word, index) => (
            <span key={word}>
              <span
                className="enter-word text-beam"
                style={{ ['--enter-delay' as string]: `${350 + index * 70}ms` }}
              >
                {word}
              </span>
              {index < HEADLINE_ACCENT.length - 1 ? ' ' : null}
            </span>
          ))}
        </p>

        <p className="enter max-w-md text-base leading-relaxed text-foreground/80 [--enter-delay:480ms]">
          Ask a hard question. Aether breaks it apart, researches every part in parallel, and writes
          a report where every factual claim carries a citation you can check.
        </p>
      </div>

      <ul className="grid max-w-lg gap-2.5">
        {FEATURES.map(({ icon: Icon, title, body }, index) => (
          <li
            key={title}
            className="auth-feature enter group"
            style={{ ['--enter-delay' as string]: `${560 + index * 80}ms` }}
          >
            <span className="icon-tile">
              <Icon className="size-[1.125rem]" aria-hidden />
            </span>
            <span className="flex flex-col gap-0.5">
              <span className="text-sm font-semibold text-foreground">{title}</span>
              <span className="text-[0.8125rem] leading-relaxed text-foreground/70">{body}</span>
            </span>
          </li>
        ))}
      </ul>

      {/* Dropped on a short screen (a 720px laptop) rather than letting the
          sign-in page scroll: it is the most expendable thing in the column. */}
      <PipelineTicker className="enter max-w-lg [--enter-delay:820ms] [@media(max-height:50rem)]:hidden" />
    </section>
  );
}
