import type { AttachmentStore } from './storage/store.js';
type SweepingStore = AttachmentStore & { sweep(now?: Date, signal?: AbortSignal): Promise<void> };
export function scheduleStorage(source: SweepingStore, clock: () => number = () => performance.now()): {store: AttachmentStore; close(): void} {
  const controller = new AbortController();
  let running = false, last = -Infinity;
  function trigger() {
    if (controller.signal.aborted || running || clock() - last < 3600000) return;
    last = clock(); running = true;
    void Promise.resolve().then(() => source.sweep(undefined, controller.signal)).catch(() => {
      console.error('Attachment cleanup failed; retaining unprocessed files.');
    }).finally(() => { running = false; });
  }
  trigger();
  return {store: {
    async stageDownload(...args) { try { return await source.stageDownload(...args); } finally { trigger(); } },
    async publish(...args) { try { return await source.publish(...args); } finally { trigger(); } },
    async discard(...args) { try { return await source.discard(...args); } finally { trigger(); } },
    async borrowSource(...args) { try { return await source.borrowSource(...args); } finally { trigger(); } },
  }, close() { controller.abort(); }};
}
