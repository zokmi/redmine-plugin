import { mock } from 'node:test';
import assert from 'node:assert/strict';
const sid = 'S-1-5-21-1-2-3-500';
let actualOwner = sid, untrusted = false;
let childProcesses = 0, objectChecks = 0, sizeQueries = 0, tokenReads = 0;
let tokenAllocations = 0, tokenFrees = 0;
const functions = {
  GetTokenInformation(token, type, data, length, size) {
    assert.equal(token, -4);
    assert.equal(type, 1);
    if (data === null) {
      sizeQueries++;
      size[0] = 48;
      return 0;
    }
    assert.equal(length, 48);
    return ++tokenReads > 1 ? 1 : 0;
  },
  GetVolumeInformationByHandleW(h, a, b, c, d, flags) { flags[0] = 8; return 1; },
  GetSecurityInfo(h, type, flags, owner, group, dacl, sacl, sd) { objectChecks++; owner[0] = actualOwner; dacl[0] = 'dacl'; sd[0] = 'sd'; return 0; },
  ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, revision, out) { out[0] = sddl; return 1; },
  ConvertSecurityDescriptorToStringSecurityDescriptorW(sd, revision, flags, out) { out[0] = `O:LAD:P(A;;FA;;;LA)${untrusted ? '(A;;FA;;;WD)' : ''}`; return 1; },
  ConvertSidToStringSidW(owner, out) { out[0] = owner; return 1; },
  LocalFree() {},
};
mock.module('koffi', {defaultExport:{
  load() { return {func(signature) { const name = Object.keys(functions).find(n => signature.includes(n+'(')); return functions[name] ?? (() => 1); }}; },
  alloc(type, length) { assert.equal(type, 'uint8_t'); tokenAllocations++; return Buffer.alloc(length); },
  free() { tokenFrees++; },
  decode: Object.assign((buffer, type) => {
    assert.ok(Buffer.isBuffer(buffer));
    assert.equal(type, 'void *');
    return sid;
  }, { string16(pointer) { return pointer; } }),
}});
mock.module('node:child_process', {namedExports:{execFileSync() { childProcesses++; return `"fixture","${sid}"\n`; }}});
const { verifyPrivateHandle, privateDescriptor, ownerDescriptor } = await import('../dist/storage/windows-acl.js');
assert.throws(() => privateDescriptor(), /Cannot inspect process SID/);
privateDescriptor().release();
ownerDescriptor().release();
for (let i = 0; i < 8; i++) verifyPrivateHandle('handle-' + i, true);
actualOwner = 'S-1-5-21-1-2-3-1001';
assert.throws(() => verifyPrivateHandle('handle', true), /Unsafe owner or DACL/);
actualOwner = 'S-1-5-32-544';
assert.throws(() => verifyPrivateHandle('handle', true), /owned by the Windows Administrators group/);
actualOwner = sid; untrusted = true;
assert.throws(() => verifyPrivateHandle('handle', true), /Unsafe DACL principal or ACE/);
assert.equal(objectChecks, 11, 'inspect every object including rejects');
assert.equal(sizeQueries, 2, 'retry the failed lookup');
assert.equal(tokenReads, 2, 'one failed read and one successful read across all descriptors and checks');
assert.equal(tokenAllocations, 2);
assert.equal(tokenFrees, tokenAllocations, 'release native token buffers on both success and failure');
assert.equal(childProcesses, 0, 'process identity lookup must not launch whoami.exe');
