import type { IsoDateTime, Uuid } from './common';

/**
 * The direct answer to the question a run was given.
 *
 * Written before the report and streamed as it is written, so a reader has
 * their answer while the report is still being assembled. What they see live
 * arrives as `answer_delta` events; this is the same text, served to anyone who
 * was not watching - a reload, a shared link, a run opened a week later.
 *
 * `content_md` is Markdown with inline `[n]` markers numbered against the
 * report's citations. On a run that never produced a report they resolve to
 * nothing and render unresolved, which is the honest rendering of a citation
 * whose chain was never validated.
 */
export interface RunAnswer {
  id: Uuid;
  run_id: Uuid;
  content_md: string;
  /** The model that answered, as the provider reported it. */
  model: string;
  word_count: number;
  /** Distinct claims the prose actually cites, counted from resolved markers. */
  citation_count: number;
  /** The model reached its output ceiling, so the answer stops early. */
  truncated: boolean;
  generated_at: IsoDateTime;
}

/**
 * `null` until the run has written one.
 *
 * Null rather than a 404: "not yet" is the normal state of a run that started
 * ten seconds ago, and a client polling a 404 cannot tell that apart from a run
 * that does not exist.
 */
export interface AnswerResponse {
  answer: RunAnswer | null;
}
