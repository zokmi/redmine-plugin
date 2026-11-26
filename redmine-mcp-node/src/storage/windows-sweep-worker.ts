import { workerData } from 'node:worker_threads';
import { createWindowsAttachmentStore } from './windows-store.js';

const { root, cancelled }: { root: string; cancelled: Int32Array } = workerData;
await createWindowsAttachmentStore({ root }).sweep(undefined, {
  get aborted() { return Atomics.load(cancelled, 0) !== 0; },
});
