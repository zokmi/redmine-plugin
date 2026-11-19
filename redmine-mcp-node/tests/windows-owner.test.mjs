import test from 'node:test';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
test('ACL verification compares canonical owner SID and still rejects foreign owners and untrusted grants', {skip:process.platform !== 'win32'}, () => {
  execFileSync(process.execPath, ['--experimental-test-module-mocks', resolve('test-support/owner-alias-fixture.mjs')], {encoding:'utf8'});
});
