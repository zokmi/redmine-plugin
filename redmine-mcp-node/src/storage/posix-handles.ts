import { closeSync, constants, fsyncSync, openSync, readlinkSync } from 'node:fs';
import { posix } from 'node:path';
import { getSystemErrorName } from 'node:util';
import koffi from 'koffi';
export { assertPrivateAcl } from './darwin-acl.js';

type RenameNew = (fromDir: number, from: string, toDir: number, to: string) => number;

function bindRenameNew(lib: ReturnType<typeof koffi.load>): RenameNew {
  if (process.platform === 'darwin') {
    const rename = lib.func('int renameatx_np(int, const char *, int, const char *, unsigned int)');
    return (a, src, b, dest) => rename(a, src, b, dest, 4); // RENAME_EXCL
  }
  try {
    const rename = lib.func('int renameat2(int, const char *, int, const char *, unsigned int)');
    return (a, src, b, dest) => rename(a, src, b, dest, 1); // RENAME_NOREPLACE
  } catch {
    // musl installations without the glibc renameat2 wrapper expose the syscall directly.
    const numbers: Record<string, number> = { x64: 316, ia32: 353, arm: 382, arm64: 276, riscv64: 276, loong64: 276, ppc64: 357, s390x: 347 };
    const nr = numbers[process.arch];
    if (nr === undefined) throw new Error('This Linux architecture requires libc renameat2');
    const syscall = lib.func('long syscall(long, ...)');
    return (a, src, b, dest) => syscall(nr, 'long', a, 'const char *', src, 'long', b, 'const char *', dest, 'long', 1);
  }
}

function bindLibc() {
  if (process.platform !== 'linux' && process.platform !== 'darwin') throw new Error('POSIX handles require Linux or macOS');
  const lib = koffi.load(null);
  return {
    openAt: lib.func('int openat(int, const char *, int, ...)'),
    mkdirAt: lib.func(`int mkdirat(int, const char *, ${process.platform === 'darwin' ? 'uint16_t' : 'uint32_t'})`),
    renameAt: lib.func('int renameat(int, const char *, int, const char *)'),
    renameNew: bindRenameNew(lib),
    unlinkAt: lib.func('int unlinkat(int, const char *, int)'),
    flock: lib.func('int flock(int, int)'),
  };
}

let bindings: ReturnType<typeof bindLibc> | undefined;
function libc() { return bindings ??= bindLibc(); }
function component(name: string): string {
  if (!name || name === '.' || name === '..' || name.includes('/') || name.includes('\0')) throw new Error('Expected one POSIX path component');
  return name;
}

function checked(name: string, call: () => number): number {
  for (;;) {
    const result = call();
    if (result !== -1) return result;
    const errno = koffi.errno();
    const code = getSystemErrorName(-errno);
    if (code !== 'EINTR') throw Object.assign(new Error(`${name}: ${code}`), { code, errno: -errno, syscall: name });
  }
}

export function openAt(parent: number, name: string, flags: number, mode = 0o600): number {
  const leaf = component(name);
  const cloexec = process.platform === 'darwin' ? 0x01000000 : 0x80000;
  const safeFlags = flags | cloexec | constants.O_NOFOLLOW | constants.O_NONBLOCK | constants.O_NOCTTY;
  return checked('openat', () => libc().openAt(parent, leaf, safeFlags, 'int', mode));
}

export function mkdirAt(parent: number, name: string, mode = 0o700): void {
  const leaf = component(name);
  checked('mkdirat', () => libc().mkdirAt(parent, leaf, mode));
}

export function renameAt(fromDir: number, from: string, toDir: number, to: string): void {
  const src = component(from), dest = component(to);
  checked('renameat', () => libc().renameAt(fromDir, src, toDir, dest));
}

export function renameNewAt(fromDir: number, from: string, toDir: number, to: string): void {
  const src = component(from), dest = component(to);
  checked('exclusive rename', () => libc().renameNew(fromDir, src, toDir, dest));
}

export function unlinkAt(parent: number, name: string, directory = false): void {
  const leaf = component(name);
  const removeDir = process.platform === 'darwin' ? 0x80 : 0x200;
  checked('unlinkat', () => libc().unlinkAt(parent, leaf, directory ? removeDir : 0));
}

export function tryFlock(fd: number): boolean {
  try {
    checked('flock', () => libc().flock(fd, 2 | 4)); // LOCK_EX | LOCK_NB
    return true;
  } catch (error) {
    if (['EAGAIN', 'EWOULDBLOCK'].includes((error as NodeJS.ErrnoException).code ?? '')) return false;
    throw error;
  }
}

function directoryComponents(path: string): string[] {
  if (path.includes('\0')) throw new Error('Invalid POSIX path');
  const parts = posix.resolve(path).split('/').filter(Boolean);
  // macOS exposes its system temporary directories through root-owned aliases.
  if (process.platform === 'darwin' && ['var', 'tmp', 'etc'].includes(parts[0])) {
    try {
      const target = readlinkSync('/' + parts[0]);
      if (target === 'private/' + parts[0] || target === '/private/' + parts[0]) parts.unshift('private');
    } catch (error) {
      if (!['EINVAL', 'ENOENT'].includes((error as NodeJS.ErrnoException).code ?? '')) throw error;
    }
  }
  return parts;
}

export function openDirectory(path: string, createMissing = false): number {
  const parts = directoryComponents(path);
  const flags = constants.O_RDONLY | constants.O_DIRECTORY;
  let fd = openSync('/', flags);
  try {
    for (const part of parts) {
      let child: number;
      try { child = openAt(fd, part, flags); }
      catch (error) {
        if (!createMissing || (error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
        try { mkdirAt(fd, part); }
        catch (mkdirError) { if ((mkdirError as NodeJS.ErrnoException).code !== 'EEXIST') throw mkdirError; }
        fsyncSync(fd);
        child = openAt(fd, part, flags);
      }
      closeSync(fd);
      fd = child;
    }
    return fd;
  } catch (error) { closeSync(fd); throw error; }
}
