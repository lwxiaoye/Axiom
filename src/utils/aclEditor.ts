export type AclSubjectType = 'USER' | 'ROLE' | 'DEPARTMENT';

export type FlatAclItem<TPermission extends string = string> = {
  subjectType: AclSubjectType;
  subjectId: string | string[];
  permission: TPermission;
};

export type AclEditorItem<TPermission extends string = string> = {
  subjectType: AclSubjectType;
  subjectIds: string[];
  permission: TPermission;
};

export type AtomicAclItem<TPermission extends string = string> = Omit<FlatAclItem<TPermission>, 'subjectId'> & {
  subjectId: string;
};

function normalizeSubjectIds(value: string | string[]): string[] {
  const values = Array.isArray(value) ? value : value.split(',');
  return Array.from(new Set(values.map((item) => String(item).trim()).filter(Boolean)));
}

export function groupAclItems<TPermission extends string>(
  items: FlatAclItem<TPermission>[],
): AclEditorItem<TPermission>[] {
  const groups = new Map<string, AclEditorItem<TPermission>>();
  for (const item of items) {
    const key = `${item.subjectType}::${item.permission}`;
    const existing = groups.get(key);
    const subjectIds = normalizeSubjectIds(item.subjectId);
    if (!existing) {
      groups.set(key, { subjectType: item.subjectType, subjectIds, permission: item.permission });
      continue;
    }
    existing.subjectIds = Array.from(new Set([...existing.subjectIds, ...subjectIds]));
  }
  return Array.from(groups.values());
}

export function expandAclItems<TPermission extends string>(
  items: AclEditorItem<TPermission>[],
): AtomicAclItem<TPermission>[] {
  return items.flatMap((item) =>
    normalizeSubjectIds(item.subjectIds).map((subjectId) => ({
      subjectType: item.subjectType,
      subjectId,
      permission: item.permission,
    })),
  );
}
