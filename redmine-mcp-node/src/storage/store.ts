import type { Writable } from 'node:stream';

/** Implementations must pin directory handles and enforce the configured source boundary. */
export interface StagedDownload { readonly writer: Writable }
export interface AttachmentStore {
  stageDownload(siteUrl: string, filename: string): Promise<StagedDownload>;
  publish(download: StagedDownload): Promise<string>;
  discard(download: StagedDownload): Promise<void>;
  borrowSource(path: string, maxBytes: number, uploadRoot: string | null): Promise<Uint8Array>;
}
export const unavailableStore: AttachmentStore = {
  async stageDownload() { throw new Error('Attachment storage unavailable'); },
  async publish() { throw new Error('Attachment storage unavailable'); },
  async discard() {},
  async borrowSource() { throw new Error('Attachment storage unavailable'); },
};
