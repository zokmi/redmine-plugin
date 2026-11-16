import koffi from 'koffi';
import path from 'node:path';
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
export function closeHandle(handle: Handle): void { if (!close || !close(handle)) throw new Error('Cannot close handle'); }
export function openChildDirectory(parent: Handle, name: string): Handle {
  if (!ntCreate || !name || name === '.' || name === '..' || /[/\\\0:]/.test(name)) throw new Error('Invalid directory component');
  const bytes = Buffer.from(name + '\0', 'utf16le');
  if (bytes.length > 65535) throw new Error('Directory component too long');
  const unicode = {Length: bytes.length - 2, MaximumLength: bytes.length, Buffer: bytes};
  const attrs = {Length: koffi.sizeof(ObjectAttributes), RootDirectory: parent, ObjectName: unicode, Attributes: 0x40 | 0x1000, SecurityDescriptor: null, SecurityQualityOfService: null};
  const output: unknown[] = [null]; const io = {};
  const result = ntCreate(output, 0x80 | 0x20 | 0x100000, attrs, io, null, 0, 3, 1, 0x20 | 0x200000 | 0x4000 | 1, null, 0);
  if (result < 0 || !output[0]) throw new Error(`Cannot open directory (NTSTATUS ${result})`);
  return verified(output[0]);
}
export function openDirectory(directory: string): Handle {
  if (!createFile || !path.win32.isAbsolute(directory) || !/^[a-zA-Z]:\\/.test(directory) || directory.includes('..')) throw new Error('Invalid directory path');
  const parts = directory.slice(3).split(/\\+/).filter(Boolean);
  let current = verified(createFile(directory.slice(0, 3), 0x80 | 0x20, 3, null, 3, 0x02000000 | 0x00200000, null));
  try { for (const part of parts) { const next = openChildDirectory(current, part); closeHandle(current); current = next; } return current; }
  catch (error) { closeHandle(current); throw error; }
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
  const result = ntCreate(output, 0xc0100000, attrs, io, null, 0, 3, 3, 0x20 | 0x200000 | 0x4000 | 0x40, null, 0);
  if (result < 0 || !output[0]) throw new Error(`Cannot open lock (NTSTATUS ${result})`);
  const handle = output[0], details: {attributes?: number} = {};
  if (!info(handle, details) || getType(handle) !== 1 || (details.attributes! & (0x10 | 0x400))) { closeHandle(handle); throw new Error('Unsafe lock handle'); }
  const overlapped = {Internal:0, InternalHigh:0, Offset:0, OffsetHigh:0, Event:null};
  if (!lockFile(handle, 3, 0, 1, 0, overlapped)) {
    const error = lastError(); closeHandle(handle);
    if (error === 33) return null;
    throw new Error(`Cannot lock file (${error})`);
  }
  return handle;
}
