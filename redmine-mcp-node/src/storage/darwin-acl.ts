import koffi from 'koffi';

function bindAcl() {
  const lib = koffi.load(null);
  return {
    get: lib.func('void *acl_get_fd(int)'),
    entry: lib.func('int acl_get_entry(void *, int, _Out_ void **)'),
    tag: lib.func('int acl_get_tag_type(void *, _Out_ int *)'),
    mask: lib.func('int acl_get_permset_mask_np(void *, _Out_ uint64_t *)'),
    qualifier: lib.func('void *acl_get_qualifier(void *)'),
    principal: lib.func('int mbr_uuid_to_id(void *, _Out_ uint32_t *, _Out_ int *)'),
    free: lib.func('int acl_free(void *)'),
  };
}

let api: ReturnType<typeof bindAcl> | undefined;
export function assertPrivateAcl(fd: number, allowPublicRead = false): void {
  if (process.platform !== 'darwin') return;
  const acl = api ??= bindAcl();
  const list = acl.get(fd);
  if (list === null) {
    // Darwin returns ENOENT when the open inode has no extended ACL.
    if (koffi.errno() === koffi.os.errno.ENOENT) return;
    throw new Error('無法驗證 macOS 檔案權限');
  }
  try {
    const entry = [null], tag = [0], mask = [0];
    for (let index = 0; ; index = -1) { // ACL_FIRST_ENTRY / ACL_NEXT_ENTRY
      if (acl.entry(list, index, entry) !== 0) {
        if (koffi.errno() === koffi.os.errno.EINVAL) break; // End of ACL on Darwin.
        throw new Error('無法驗證 macOS 檔案權限');
      }
      if (acl.tag(entry[0], tag) !== 0 || acl.mask(entry[0], mask) !== 0) throw new Error('無法驗證 macOS 檔案權限');
      // BSD mode does not mask Darwin allow ACEs. Check data, execute/search, delete,
      // writes to metadata/security and ownership grants. Read-only attributes are harmless.
      const mutations = 0x74 | (1 << 8) | (1 << 10) | (1 << 12) | (1 << 13);
      const sensitive = allowPublicRead ? mutations : mutations | (1 << 1) | (1 << 3);
      if (tag[0] !== 1 || (Number(mask[0]) & sensitive) === 0) continue;
      const uuid = acl.qualifier(entry[0]);
      if (uuid === null) throw new Error('無法驗證 macOS 檔案權限');
      try {
        const id = [0], type = [0];
        if (acl.principal(uuid, id, type) !== 0 || type[0] !== 0 || (id[0] !== 0 && id[0] !== process.geteuid!())) {
          throw new Error('macOS ACL 允許其他帳號存取私人設定或附件');
        }
      } finally { acl.free(uuid); }
    }
  } finally { acl.free(list); }
}
