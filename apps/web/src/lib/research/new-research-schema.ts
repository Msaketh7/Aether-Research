import { RESEARCH_MODES, type CreateResearchRequest } from '@aether/shared-types';
import { z } from 'zod';

/**
 * Client-side validation for the new-research form (FR-2).
 *
 * This is a convenience layer, not a security control: the API validates the
 * same rules again, and the server's answer wins. Keeping the rules here in one
 * schema means the form, its tests and the request builder cannot drift.
 */

/** A domain hint like `sec.gov`; not a URL, and never a scheme. */
const DOMAIN_PATTERN = /^(?!-)[a-z0-9-]+(\.[a-z0-9-]+)+$/i;

export const newResearchSchema = z
  .object({
    question: z
      .string()
      .trim()
      .min(15, 'Describe the research question in at least 15 characters.')
      .max(2000, 'Keep the question under 2000 characters.'),
    mode: z.enum(RESEARCH_MODES),
    depth: z.number().int().min(1).max(5),
    domains: z
      .array(z.string())
      .max(10, 'At most 10 domain filters.')
      .refine(
        (domains) => domains.every((domain) => DOMAIN_PATTERN.test(domain)),
        'Enter bare domains such as sec.gov, without http:// or a path.',
      ),
    dateRangeStart: z.string().optional(),
    dateRangeEnd: z.string().optional(),
  })
  .refine(
    (value) =>
      !value.dateRangeStart || !value.dateRangeEnd || value.dateRangeStart <= value.dateRangeEnd,
    { message: 'The end date must not be before the start date.', path: ['dateRangeEnd'] },
  );

export type NewResearchValues = z.infer<typeof newResearchSchema>;

export const NEW_RESEARCH_DEFAULTS: NewResearchValues = {
  question: '',
  mode: 'deep',
  depth: 3,
  domains: [],
  dateRangeStart: '',
  dateRangeEnd: '',
};

/** Flattens Zod issues into the same shape the API's error envelope uses. */
export function fieldErrorsOf(error: z.ZodError): Record<string, string[]> {
  const result: Record<string, string[]> = {};
  for (const issue of error.issues) {
    const key = String(issue.path[0] ?? 'form');
    (result[key] ??= []).push(issue.message);
  }
  return result;
}

export function toCreateRequest(values: NewResearchValues): CreateResearchRequest {
  return {
    question: values.question.trim(),
    mode: values.mode,
    // Depth is meaningless for a single-pass quick run; do not send noise.
    ...(values.mode === 'deep' ? { depth: values.depth } : {}),
    ...(values.domains.length > 0 ? { domains: values.domains } : {}),
    ...(values.dateRangeStart ? { date_range_start: values.dateRangeStart } : {}),
    ...(values.dateRangeEnd ? { date_range_end: values.dateRangeEnd } : {}),
  };
}

/** Normalises free text into a domain hint, or null when it cannot be one. */
export function normalizeDomain(raw: string): string | null {
  const cleaned = raw
    .trim()
    .toLowerCase()
    .replace(/^https?:\/\//, '')
    .replace(/^www\./, '')
    .replace(/\/.*$/, '');
  return cleaned && DOMAIN_PATTERN.test(cleaned) ? cleaned : null;
}
