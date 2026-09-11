import type { IsoDateTime, Uuid } from './common';
import type { DocumentFormat } from './enums';

/**
 * A document the user uploaded with `POST /files`. Its `id` is what
 * `CreateResearchRequest.document_ids` refers to.
 *
 * Uploading stores the file. A run created with it ingests the file into that
 * run's sources when the run executes.
 */
export interface UploadedFile {
  id: Uuid;
  filename: string;
  format: DocumentFormat;
  mime_type: string;
  size_bytes: number;
  /** SHA-256 of the bytes as uploaded. The same file uploaded twice is one upload. */
  content_hash: string;
  created_at: IsoDateTime;
}
