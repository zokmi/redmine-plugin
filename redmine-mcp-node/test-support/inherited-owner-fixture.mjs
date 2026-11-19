import { mock } from 'node:test';
import assert from 'node:assert/strict';
let directory = true, releases = 0, creating = true;
const functions = {
  NtCreateFile(out, access, attributes) { assert.equal(attributes.SecurityDescriptor, creating ? 'owner-descriptor' : null); if (failCreate) throw new Error('injected creation failure'); out[0] = 'handle'; return 0; },
  GetFileInformationByHandle(h, info) { Object.assign(info,{attributes:directory ? 0x10 : 0, links:1}); return 1; },
  GetFileType() { return 1; },
};
mock.module('koffi', {defaultExport:{
  load() { return {func(signature) { const name = Object.keys(functions).find(n => signature.includes(n+'(')); return functions[name] ?? (() => 1); }}; },
  struct(name) { return name; }, pointer(type) { return type; }, sizeof() { return 48; },
}});
mock.module(new URL('../dist/storage/windows-acl.js', import.meta.url), {namedExports:{
  ownerDescriptor() { return {descriptor:'owner-descriptor',release(){releases++;}}; },
  privateDescriptor() { throw new Error('Should inherit parent DACL'); }, verifyPrivateHandle() {},
}});
const native = await import('../dist/storage/windows-handles.js');
native.createDirectory('parent','child');
directory = false;
native.openFile('parent','file','create');
native.openFile('parent','lock','openOrCreate');
native.openDurableFile('parent','durable','create');
creating = false;
native.openFile('parent','existing','open');
assert.equal(releases,4);

creating = true; failCreate = true;
assert.throws(() => native.openFile('parent','failed','create'), /injected creation failure/);
assert.equal(releases,5);
