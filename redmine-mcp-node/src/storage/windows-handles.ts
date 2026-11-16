import koffi from 'koffi';
import path from 'node:path';
import { privateDescriptor, verifyPrivateHandle } from './windows-acl.js';
export { verifyPrivateHandle } from './windows-acl.js';
const kernel = process.platform === 'win32' ? koffi.load('kernel32.dll') : null;
const nt = process.platform === 'win32' ? koffi.load('ntdll.dll') : null;
const UnicodeString = koffi.struct('RM_UNICODE_STRING', { Length: 'uint16', MaximumLength: 'uint16', Buffer: 'void *' });
const ObjectAttributes = koffi.struct('RM_OBJECT_ATTRIBUTES', { Length: 'uint32', RootDirectory: 'void *', ObjectName: koffi.pointer(UnicodeString), Attributes: 'uint32', SecurityDescriptor: 'void *', SecurityQualityOfService: 'void *' });
const IoStatus = koffi.struct('RM_IO_STATUS', { Status: 'intptr_t', Information: 'uintptr_t' });
const FileInfo = koffi.struct('RM_FILE_INFO', { attributes:'uint32', creationLow:'uint32', creationHigh:'uint32', accessLow:'uint32', accessHigh:'uint32', writeLow:'uint32', writeHigh:'uint32', volume:'uint32', sizeHigh:'uint32', sizeLow:'uint32', links:'uint32', indexHigh:'uint32', indexLow:'uint32' });
void IoStatus; void FileInfo;
const createFile = kernel?.func('void * __stdcall CreateFileW(const char16_t *, uint32, uint32, void *, uint32, uint32, void *)');
const close = kernel?.func('int __stdcall CloseHandle(void *)');
const info = kernel?.func('int __stdcall GetFileInformationByHandle(void *, _Out_ RM_FILE_INFO *)');
const volumeInfo = kernel?.func('int __stdcall GetVolumeInformationByHandleW(void *, void *, uint32, void *, void *, void *, _Out_ void *, uint32)');
const ntCreate = nt?.func('int32 __stdcall NtCreateFile(_Out_ void **, uint32, RM_OBJECT_ATTRIBUTES *, _Out_ RM_IO_STATUS *, void *, uint32, uint32, uint32, uint32, void *, uint32)');
type Handle = unknown;
function verified(handle: Handle): Handle {
  const details: {attributes?: number} = {};
  if (!info || !info(handle, details) || !(details.attributes! & 0x10) || (details.attributes! & 0x400)) {
    if (handle) close?.(handle);
    throw new Error('Unsafe directory handle');
  }
  return handle;
}
const ancestorChains = new Map<Handle, Handle[]>();
export function closeHandle(handle: Handle): void {
  const chain = ancestorChains.get(handle); ancestorChains.delete(handle);
  let failed = false;
  for (const item of [handle, ...(chain?.reverse() ?? [])]) {
    try { if (!close || !close(item)) failed = true; } catch { failed = true; }
  }
  if (failed) throw new Error('Cannot close handle');
}
export function openChildDirectory(parent: Handle, name: string): Handle {
  if (!ntCreate || !name || name === '.' || name === '..' || /[/\\\0:]/.test(name)) throw new Error('Invalid directory component');
  const bytes = Buffer.from(name + '\0', 'utf16le');
  if (bytes.length > 65535) throw new Error('Directory component too long');
  const unicode = {Length: bytes.length - 2, MaximumLength: bytes.length, Buffer: bytes};
  const attrs = {Length: koffi.sizeof(ObjectAttributes), RootDirectory: parent, ObjectName: unicode, Attributes: 0x40 | 0x1000, SecurityDescriptor: null, SecurityQualityOfService: null};
  const output: unknown[] = [null]; const io = {};
  const result = ntCreate(output, 0x80 | 0x20 | 0x100000 | 0x20000, attrs, io, null, 0, 3, 1, 0x20 | 0x200000 | 0x4000 | 1, null, 0);
  if (result < 0 || !output[0]) throw new Error(`Cannot open directory (NTSTATUS ${result})`);
  return verified(output[0]);
}
export function openDirectory(directory: string): Handle {
  if (!createFile || !path.win32.isAbsolute(directory) || !/^[a-zA-Z]:\\/.test(directory) || directory.includes('..')) throw new Error('Invalid directory path');
  const parts = directory.slice(3).split(/\\+/).filter(Boolean);
  let current = verified(createFile(directory.slice(0, 3), 0x80 | 0x20, 3, null, 3, 0x02000000 | 0x00200000, null));
  const chain: Handle[] = [];
  try { for (const part of parts) { const next = openChildDirectory(current, part); chain.push(current); current = next; } ancestorChains.set(current, chain); return current; }
  catch (error) {
    for (const handle of [current, ...chain.reverse()]) {
      try { closeHandle(handle); } catch { /* Preserve the traversal failure while attempting every close. */ }
    }
    throw error;
  }
}

const Overlapped = koffi.struct('RM_OVERLAPPED', { Internal:'uintptr_t', InternalHigh:'uintptr_t', Offset:'uint32', OffsetHigh:'uint32', Event:'void *' });
void Overlapped;
const lockFile = kernel?.func('int __stdcall LockFileEx(void *, uint32, uint32, uint32, uint32, RM_OVERLAPPED *)');
const lastError = kernel?.func('uint32 __stdcall GetLastError()');
const getType = kernel?.func('uint32 __stdcall GetFileType(void *)');
/** Internal experimental primitive; caller owns the returned locked handle. */
export function tryLock(parent: Handle, name: string): Handle | null {
  if (!ntCreate || !lockFile || !lastError || !info || !getType || !name || name === '.' || name === '..' || /[/\\\0:]/.test(name)) throw new Error('Invalid lock name');
  const bytes = Buffer.from(name + '\0', 'utf16le');
  if (bytes.length > 65535) throw new Error('Lock name too long');
  const attrs = {Length:koffi.sizeof(ObjectAttributes), RootDirectory:parent, ObjectName:{Length:bytes.length - 2, MaximumLength:bytes.length, Buffer:bytes}, Attributes:0x40 | 0x1000, SecurityDescriptor:null, SecurityQualityOfService:null};
  const output: unknown[] = [null]; const io = {};
  const result = ntCreate(output, 0xc0120000, attrs, io, null, 0, 3, 3, 0x20 | 0x200000 | 0x4000 | 0x40, null, 0);
  if (result < 0 || !output[0]) throw new Error(`Cannot open lock (NTSTATUS ${result})`);
  const handle = output[0];
  try { verifyRegularFile(handle); } catch (error) { closeHandle(handle); throw error; }
  const overlapped = {Internal:0, InternalHigh:0, Offset:0, OffsetHigh:0, Event:null};
  if (!lockFile(handle, 3, 0, 1, 0, overlapped)) {
    const error = lastError(); closeHandle(handle);
    if (error === 33) return null;
    throw new Error(`Cannot lock file (${error})`);
  }
  return handle;
}

const ntSet = nt?.func('int32 __stdcall NtSetInformationFile(void *, _Out_ RM_IO_STATUS *, void *, uint32, uint32)');
const readFile = kernel?.func('int __stdcall ReadFile(void *, _Out_ void *, uint32, _Out_ uint32 *, void *)');
const writeFile = kernel?.func('int __stdcall WriteFile(void *, const void *, uint32, _Out_ uint32 *, void *)');
const flushFile = kernel?.func('int __stdcall FlushFileBuffers(void *)');
const setFilePointer = kernel?.func('int __stdcall SetFilePointerEx(void *, int64, _Out_ int64 *, uint32)');
function verifyRegularFile(handle: Handle): void {
  const details: {attributes?: number, links?: number} = {};
  if (!info || !getType || !info(handle, details) || getType(handle) !== 1 || (details.attributes! & (0x10 | 0x400)) || details.links !== 1) throw new Error('Unsafe file handle');
}
function relative(parent: Handle, name: string, access: number, disposition: number, directory: boolean, descriptor: unknown = null, share = 3, writeThrough = false): Handle {
  if (!ntCreate || !info || !getType || !name || name === '.' || name === '..' || /[/\\\0:]/.test(name)) throw new Error('Invalid component');
  const bytes = Buffer.from(name + '\0', 'utf16le');
  if (bytes.length > 65535) throw new Error('Component too long');
  const attrs = {Length:koffi.sizeof(ObjectAttributes), RootDirectory:parent, ObjectName:{Length:bytes.length - 2, MaximumLength:bytes.length, Buffer:bytes}, Attributes:0x40 | 0x1000, SecurityDescriptor:null, SecurityQualityOfService:null};
  attrs.SecurityDescriptor = descriptor as null;
  const output: unknown[] = [null];
  const result = ntCreate(output, access | 0x100000 | 0x20000, attrs, {}, null, 0, share, disposition, 0x20 | 0x200000 | 0x4000 | (writeThrough ? 2 : 0) | (directory ? 1 : 0x40), null, 0);
  if (result < 0 || !output[0]) throw new Error(`Cannot open entry (NTSTATUS ${result})`);
  const handle = output[0], details: {attributes?: number} = {};
  try {
    if (directory) {
      if (!info(handle, details) || getType(handle) !== 1 || (details.attributes! & 0x400) || !(details.attributes! & 0x10)) throw new Error('Unsafe entry handle');
    } else verifyRegularFile(handle);
  } catch (error) { closeHandle(handle); throw error; }
  return handle;
}
/** Caller closes each returned handle. */
export function openFile(parent: Handle, name: string, mode: 'open' | 'create' | 'openOrCreate' = 'open', access: 'read' | 'modify' = 'modify'): Handle {
  if (access === 'read' && mode !== 'open') throw new Error('Read-only handle must open an existing file');
  return relative(parent, name, access === 'read' ? 0x80000080 : 0xc0010080, mode === 'open' ? 1 : mode === 'create' ? 2 : 3, false);
}
/** Pins an existing regular file against concurrent write and replacement; caller verifies its ACL. */
export function openGuardedFile(parent: Handle, name: string): Handle {
  return relative(parent, name, 0x80010080, 1, false, null, 1);
}
/** Exclusive read/write/delete handle with FILE_WRITE_THROUGH for lifecycle barriers. */
export function openDurableFile(parent: Handle, name: string, mode: 'open' | 'create'): Handle {
  return relative(parent, name, 0xc0010080, mode === 'open' ? 1 : 2, false, null, 0, true);
}
export function openDurableDirectory(parent: Handle, name: string): Handle {
  return relative(parent, name, 0xc0000080, 1, true, null, 3, true);
}
/** Refuse destructive lifecycle actions where NTFS metadata guarantees are unavailable. */
export function verifyNtfs(handle: Handle): void {
  const filesystem = Buffer.alloc(512);
  if (!volumeInfo || !volumeInfo(handle, null, 0, null, null, null, filesystem, filesystem.length / 2)) throw new Error('Cannot determine filesystem');
  if (filesystem.toString('utf16le').split('\0', 1)[0] !== 'NTFS') throw new Error('Lifecycle requires NTFS');
}
export function seekHandle(handle: Handle, offset: number): void {
  if (!setFilePointer || !Number.isSafeInteger(offset) || offset < 0) throw new Error('Invalid file offset');
  verifyRegularFile(handle);
  if (!setFilePointer(handle, offset, null, 0)) throw new Error(`Cannot seek file (${lastError?.()})`);
}
export function createDirectory(parent: Handle, name: string): Handle { return relative(parent, name, 0x80 | 0x20, 2, true); }
export function createPrivateDirectory(parent: Handle, name: string): Handle {
  const sd = privateDescriptor();
  try { const handle = relative(parent, name, 0x80 | 0x20, 2, true, sd.descriptor); try { verifyPrivateHandle(handle); return handle; } catch (error) { closeHandle(handle); throw error; } } finally { sd.release(); }
}
export function readHandle(handle: Handle, length: number): Buffer {
  if (!readFile || !Number.isSafeInteger(length) || length < 0 || length > 0x7fffffff) throw new Error('Invalid read length');
  verifyRegularFile(handle);
  const buffer = Buffer.alloc(length), count = [0];
  if (!readFile(handle, buffer, length, count, null)) throw new Error(`Cannot read file (${lastError?.()})`);
  return buffer.subarray(0, count[0]);
}
export function writeHandle(handle: Handle, data: Buffer): void {
  if (!writeFile) throw new Error('Windows only');
  let offset = 0;
  while (offset < data.length) {
    verifyRegularFile(handle);
    const count = [0];
    if (!writeFile(handle, data.subarray(offset), data.length - offset, count, null) || count[0] === 0) throw new Error(`Cannot write file (${lastError?.()})`);
    offset += count[0];
  }
}
export function flushHandle(handle: Handle): void { if (!flushFile || !flushFile(handle)) throw new Error(`Cannot flush file (${lastError?.()})`); }
export function renameHandle(handle: Handle, parent: Handle, name: string, replace = false): void {
  if (!ntSet || !name || name === '.' || name === '..' || /[/\\\0:]/.test(name)) throw new Error('Invalid component');
  verifyRegularFile(handle);
  const bytes = Buffer.from(name, 'utf16le');
  if (bytes.length > 65533) throw new Error('Component too long');
  const Rename = koffi.struct({ Replace:'int32', Root:'void *', Length:'uint32', Name:koffi.array('uint8', bytes.length) });
  const buffer = Buffer.alloc(koffi.sizeof(Rename));
  koffi.encode(buffer, Rename, {Replace:replace ? 1 : 0, Root:parent, Length:bytes.length, Name:bytes});
  const result = ntSet(handle, {}, buffer, buffer.length, 10);
  if (result < 0) throw new Error(`Cannot rename file (NTSTATUS ${result})`);
}
export function deleteHandle(handle: Handle): void {
  if (!ntSet) throw new Error('Windows only');
  verifyRegularFile(handle);
  const result = ntSet(handle, {}, Buffer.from([1]), 1, 13);
  if (result < 0) throw new Error(`Cannot delete file (NTSTATUS ${result})`);
}
