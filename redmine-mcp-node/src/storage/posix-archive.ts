import { createHash } from 'node:crypto';
import { fstatSync, fsyncSync, read, writeSync } from 'node:fs';
import { createRequire } from 'node:module';
import { Readable, Writable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { promisify } from 'node:util';
import { ZipFile as ZipWriter } from 'yazl';
import { RandomAccessReader, fromRandomAccessReaderPromise } from 'yauzl';
import type { ItemRecord } from './record.js';

const crc32 = createRequire(import.meta.url)('buffer-crc32') as { unsigned(data: Buffer, previous: number): number };
const readAt = promisify(read);

export function signature(fd: number): string {
  const s = fstatSync(fd, { bigint: true });
  return [s.dev, s.ino, s.size, s.mtimeNs, s.ctimeNs].join('/');
}

export function writeAll(fd: number, data: Buffer): void {
  let offset = 0;
  while (offset < data.length) {
    const n = writeSync(fd, data, offset, data.length - offset);
    if (!n) throw new Error('No progress writing attachment');
    offset += n;
  }
}

function range(fd: number, start: number, end: number): Readable {
  async function* bytes() {
    let pos = start;
    while (pos < end) {
      const chunk = Buffer.allocUnsafe(Math.min(65536, end - pos));
      const { bytesRead } = await readAt(fd, chunk, 0, chunk.length, pos);
      if (!bytesRead) throw new Error('Attachment changed during read');
      pos += bytesRead;
      yield chunk.subarray(0, bytesRead);
    }
  }
  return Readable.from(bytes());
}

export async function fingerprint(fd: number) {
  const before = signature(fd), size = fstatSync(fd).size, hash = createHash('sha256');
  if (!Number.isSafeInteger(size)) throw new Error('Attachment too large');
  for await (const chunk of range(fd, 0, size)) hash.update(chunk);
  if (signature(fd) !== before) throw new Error('Attachment changed during hashing');
  return { size, sha256: hash.digest('hex') };
}

class ArchiveReader extends RandomAccessReader {
  constructor(private readonly fd: number) { super(); }
  _readStreamForRange(start: number, end: number): Readable { return range(this.fd, start, end); }
  close(done: (error: Error | null) => void): void { done(null); }
}

export async function verifyArchive(fd: number, record: ItemRecord): Promise<string> {
  const stamp = signature(fd);
  const zip = await fromRandomAccessReaderPromise(new ArchiveReader(fd), fstatSync(fd).size, {
    lazyEntries: true, autoClose: false, strictFileNames: true,
  });
  try {
    if (zip.entryCount !== 1) throw new Error('Unexpected ZIP entry count');
    let entries = 0;
    for await (const entry of zip.eachEntry()) {
      if (++entries !== 1 || entry.fileName !== record.filename || entry.uncompressedSize !== record.size) {
        throw new Error('ZIP member mismatch');
      }
      const stream = await zip.openReadStreamPromise(entry), hash = createHash('sha256');
      let size = 0, crc = 0;
      for await (const chunk of stream) {
        size += chunk.length;
        if (size > record.size) throw new Error('ZIP exceeds recorded size');
        hash.update(chunk);
        crc = crc32.unsigned(chunk, crc);
      }
      if (size !== record.size || crc !== entry.crc32 || hash.digest('hex') !== record.sha256) {
        throw new Error('ZIP content mismatch');
      }
    }
    if (entries !== 1 || signature(fd) !== stamp) throw new Error('ZIP changed during validation');
    return stamp;
  } finally { zip.close(); }
}

export async function writeArchive(source: number, fd: number, record: ItemRecord): Promise<void> {
  const writer = new ZipWriter();
  const output = writer.outputStream as Readable;
  const input = range(source, 0, record.size);
  input.on('error', error => output.destroy(error));
  writer.on('error', error => output.destroy(error));
  writer.addReadStream(input, record.filename, { size: record.size, forceZip64Format: true });
  writer.end();
  await pipeline(output, new Writable({
    write(chunk, _encoding, done) {
      try { writeAll(fd, chunk); done(); } catch (error) { done(error as Error); }
    },
  }));
  fsyncSync(fd);
  await verifyArchive(fd, record);
}
