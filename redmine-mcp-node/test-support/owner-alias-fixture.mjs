import { mock } from 'node:test';
import assert from 'node:assert/strict';
const sid = 'S-1-5-21-1-2-3-500';
let actualOwner = sid, untrusted = false;
const functions = {
  GetSystemDirectoryW(buffer) { const directory = 'C:\\Windows\\System32'; buffer.write(directory, 'utf16le'); return directory.length; },
  GetVolumeInformationByHandleW(h, a, b, c, d, flags) { flags[0] = 8; return 1; },
  GetSecurityInfo(h, type, flags, owner, group, dacl, sacl, sd) { owner[0] = actualOwner; dacl[0] = 'dacl'; sd[0] = 'sd'; return 0; },
  ConvertSecurityDescriptorToStringSecurityDescriptorW(sd, revision, flags, out) { out[0] = `O:LAD:P(A;;FA;;;LA)${untrusted ? '(A;;FA;;;WD)' : ''}`; return 1; },
  ConvertSidToStringSidW(owner, out) { out[0] = owner; return 1; },
  LocalFree() {},
};
mock.module('koffi', {defaultExport:{
  load() { return {func(signature) { const name = Object.keys(functions).find(n => signature.includes(n+'(')); return functions[name] ?? (() => 1); }}; },
  decode:{string16(pointer) { return pointer; }},
}});
mock.module('node:child_process', {namedExports:{execFileSync() { return `"fixture","${sid}"\n`; }}});
const { verifyPrivateHandle } = await import('../dist/storage/windows-acl.js');
verifyPrivateHandle('handle', true);
actualOwner = 'S-1-5-21-1-2-3-1001';
assert.throws(() => verifyPrivateHandle('handle', true), /Unsafe owner or DACL/);
actualOwner = sid; untrusted = true;
assert.throws(() => verifyPrivateHandle('handle', true), /Unsafe DACL principal or ACE/);
