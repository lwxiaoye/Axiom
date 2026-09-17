import { expandAclItems, groupAclItems } from '../../utils/aclEditor';

describe('ACL editor conversion', () => {
  it('groups flat records by subject type and permission for multi-select editing', () => {
    expect(
      groupAclItems([
        { subjectType: 'DEPARTMENT', subjectId: 'dept-1', permission: 'VIEWER' },
        { subjectType: 'DEPARTMENT', subjectId: 'dept-2', permission: 'VIEWER' },
        { subjectType: 'DEPARTMENT', subjectId: 'dept-3', permission: 'EDITOR' },
        { subjectType: 'ROLE', subjectId: 'role-1', permission: 'VIEWER' },
      ]),
    ).toEqual([
      { subjectType: 'DEPARTMENT', subjectIds: ['dept-1', 'dept-2'], permission: 'VIEWER' },
      { subjectType: 'DEPARTMENT', subjectIds: ['dept-3'], permission: 'EDITOR' },
      { subjectType: 'ROLE', subjectIds: ['role-1'], permission: 'VIEWER' },
    ]);
  });

  it('expands grouped editor values into atomic ACL records when saving', () => {
    expect(
      expandAclItems([
        { subjectType: 'USER', subjectIds: ['user-1', 'user-2'], permission: 'VIEWER' },
        { subjectType: 'ROLE', subjectIds: ['role-1'], permission: 'EDITOR' },
      ]),
    ).toEqual([
      { subjectType: 'USER', subjectId: 'user-1', permission: 'VIEWER' },
      { subjectType: 'USER', subjectId: 'user-2', permission: 'VIEWER' },
      { subjectType: 'ROLE', subjectId: 'role-1', permission: 'EDITOR' },
    ]);
  });
});
