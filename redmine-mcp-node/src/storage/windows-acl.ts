import koffi from 'koffi';
import { AdministratorsOwnedItemError } from './store.js';
const a = process.platform === 'win32' ? koffi.load('advapi32.dll') : null;
const k = process.platform === 'win32' ? koffi.load('kernel32.dll') : null;
const free = k?.func('void * __stdcall LocalFree(void *)');
const tokenInformation = a?.func('int __stdcall GetTokenInformation(intptr_t, int, void *, uint32, _Out_ uint32 *)');
const descriptor = a?.func('int __stdcall ConvertStringSecurityDescriptorToSecurityDescriptorW(const char16_t *, uint32, _Out_ void **, void *)');
const stringifySid = a?.func('int __stdcall ConvertSidToStringSidW(void *, _Out_ void **)');
const stringify = a?.func('int __stdcall ConvertSecurityDescriptorToStringSecurityDescriptorW(void *, uint32, uint32, _Out_ void **, void *)');
const securityInfo = a?.func('uint32 __stdcall GetSecurityInfo(void *, int, uint32, _Out_ void **, void *, _Out_ void **, void *, _Out_ void **)');
const volumeInfo = k?.func('int __stdcall GetVolumeInformationByHandleW(void *, void *, uint32, void *, void *, _Out_ uint32 *, void *, uint32)');
function sidString(value: unknown, subject: 'process' | 'owner'): string {
  const out: unknown[] = [null];
  try {
    if (!value || !stringifySid?.(value, out) || !out[0]) throw new Error(`Cannot inspect ${subject} SID`);
    return koffi.decode.string16(out[0]);
  } finally { if (out[0]) free?.(out[0]); }
}

let processUserSid: string | undefined;
function currentSid(): string {
  if (processUserSid !== undefined) return processUserSid;
  // GetCurrentProcessToken() is a non-owning pseudo-handle defined by the Windows SDK.
  const processToken = -4, tokenUser = 1, size = [0];
  tokenInformation?.(processToken, tokenUser, null, 0, size);
  if (!size[0]) throw new Error('Cannot inspect process SID');
  // TOKEN_USER starts with a pointer into its own buffer. Keep the allocation
  // stable across both native calls and cache only the copied string in this isolate.
  const user = koffi.alloc('uint8_t', size[0]);
  try {
    if (!tokenInformation?.(processToken, tokenUser, user, size[0], size)) throw new Error('Cannot inspect process SID');
    processUserSid = sidString(koffi.decode(user, 'void *'), 'process');
    return processUserSid;
  } finally { koffi.free(user); }
}
function securityDescriptor(sddl: string): {descriptor: unknown, release: () => void} {
  const out: unknown[] = [null];
  if (!descriptor?.(sddl, 1, out, null) || !out[0]) throw new Error('Cannot create security descriptor');
  return {descriptor: out[0], release: () => { free?.(out[0]); }};
}
export function privateDescriptor(): {descriptor: unknown, release: () => void} {
  const sid = currentSid();
  return securityDescriptor(`O:${sid}D:P(A;OICI;FA;;;${sid})`);
}
/** Explicit user ownership while leaving the parent DACL inheritance unchanged. */
export function ownerDescriptor(): {descriptor: unknown, release: () => void} {
  return securityDescriptor(`O:${currentSid()}`);
}
/** Strict allow-list: owner, LOCAL SYSTEM and built-in Administrators only. */
export function verifyPrivateHandle(handle: unknown, requireProtected = false): void {
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
      const ownerSid = sidString(owner[0], 'owner');
      if (ownerSid !== sid && ownerSid === 'S-1-5-32-544') {
        throw new AdministratorsOwnedItemError();
      }
      if (!match || ownerSid !== sid || !match[3] || (requireProtected && !match[2].includes('P'))) throw new Error('Unsafe owner or DACL');
      const aces = match[3].match(/\([^()]*\)/g) ?? [];
      if (!aces.length || aces.join('') !== match[3]) throw new Error('Unsafe DACL');
      // The serialized owner alias is trusted only after its actual SID matches this user.
      const trusted = new Set([sid, match[1], 'SY', 'BA', 'S-1-5-18', 'S-1-5-32-544']);
      for (const ace of aces) {
        const fields = ace.slice(1, -1).split(';');
        if (fields.length !== 6 || (fields[0] !== 'A' && fields[0] !== 'D') || (fields[0] === 'A' && !trusted.has(fields[5])) ) throw new Error('Unsafe DACL principal or ACE');
      }
    } finally { free?.(out[0]); }
  } finally { if (sd[0]) free?.(sd[0]); }
}
