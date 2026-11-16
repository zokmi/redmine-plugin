import koffi from 'koffi';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
const a = process.platform === 'win32' ? koffi.load('advapi32.dll') : null;
const k = process.platform === 'win32' ? koffi.load('kernel32.dll') : null;
const free = k?.func('void * __stdcall LocalFree(void *)');
const systemDirectory = k?.func('uint32 __stdcall GetSystemDirectoryW(_Out_ void *, uint32)');
const descriptor = a?.func('int __stdcall ConvertStringSecurityDescriptorToSecurityDescriptorW(const char16_t *, uint32, _Out_ void **, void *)');
const stringify = a?.func('int __stdcall ConvertSecurityDescriptorToStringSecurityDescriptorW(void *, uint32, uint32, _Out_ void **, void *)');
const securityInfo = a?.func('uint32 __stdcall GetSecurityInfo(void *, int, uint32, _Out_ void **, void *, _Out_ void **, void *, _Out_ void **)');
const volumeInfo = k?.func('int __stdcall GetVolumeInformationByHandleW(void *, void *, uint32, void *, void *, _Out_ uint32 *, void *, uint32)');
function currentSid(): string {
  const buffer = Buffer.alloc(32768 * 2);
  const length = systemDirectory?.(buffer, 32768) ?? 0;
  if (!length || length >= 32768) throw new Error('Cannot locate Windows system directory');
  const result = execFileSync(path.win32.join(buffer.subarray(0, length * 2).toString('utf16le'), 'whoami.exe'), ['/user', '/fo', 'csv', '/nh'], {encoding:'utf8', windowsHide:true});
  const match = /,"(S-1-(?:[0-9]+-)*[0-9]+)"\s*$/i.exec(result.trim());
  if (!match) throw new Error('Cannot inspect process SID');
  return match[1];
}
export function privateDescriptor(): {descriptor: unknown, release: () => void} {
  const out: unknown[] = [null], sid = currentSid();
  if (!descriptor?.(`O:${sid}D:P(A;OICI;FA;;;${sid})`, 1, out, null) || !out[0]) throw new Error('Cannot create private security descriptor');
  return {descriptor: out[0], release: () => { free?.(out[0]); }};
}
/** Strict allow-list: owner, LOCAL SYSTEM and built-in Administrators only. */
export function verifyPrivateHandle(handle: unknown): void {
  const flags = [0];
  if (!volumeInfo?.(handle, null, 0, null, null, flags, null, 0) || !(flags[0] & 8)) throw new Error('Volume does not support persistent ACLs');
  const owner: unknown[] = [null], dacl: unknown[] = [null], sd: unknown[] = [null];
  try {
    if (!securityInfo || securityInfo(handle, 1, 5, owner, null, dacl, null, sd) !== 0 || !sd[0] || !owner[0] || !dacl[0]) throw new Error('Cannot inspect security descriptor');
    const out: unknown[] = [null];
    if (!stringify?.(sd[0], 1, 5, out, null) || !out[0]) throw new Error('Cannot inspect DACL');
    try {
      const sddl = koffi.decode.string16(out[0]);
      const match = /^O:([^:]+)D:([A-Z]*)(\(.*\))?$/.exec(sddl);
      const sid = currentSid();
      if (!match || match[1] !== sid || !match[3]) throw new Error('Unsafe owner or DACL');
      const aces = match[3].match(/\([^()]*\)/g) ?? [];
      if (!aces.length || aces.join('') !== match[3]) throw new Error('Unsafe DACL');
      const trusted = new Set([sid, 'SY', 'BA', 'S-1-5-18', 'S-1-5-32-544']);
      for (const ace of aces) {
        const fields = ace.slice(1, -1).split(';');
        if (fields.length !== 6 || (fields[0] !== 'A' && fields[0] !== 'D') || (fields[0] === 'A' && !trusted.has(fields[5])) ) throw new Error('Unsafe DACL principal or ACE');
      }
    } finally { free?.(out[0]); }
  } finally { if (sd[0]) free?.(sd[0]); }
}
