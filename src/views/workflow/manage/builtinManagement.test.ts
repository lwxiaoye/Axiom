import { getManagedBuiltinCatalogId } from './builtinManagement';

describe('catalogue management destination', () => {
  it('routes existing custom and external assistants using their catalogue row id', () => {
    expect(getManagedBuiltinCatalogId({ id: 'catalog-1', appType: 'custom', pcUrl: '/center/chat/campus/' }))
      .toBe('builtin-catalog:catalog-1');
    expect(getManagedBuiltinCatalogId({ id: 'catalog-2', appType: 'external', h5Url: '/center/chat/interview' }))
      .toBe('builtin-catalog:catalog-2');
  });

  it('keeps ordinary applications and ambiguous or unregistered routes in their existing manager', () => {
    for (const record of [
      { id: '1', appType: 'agent', pcUrl: '/center/chat/campus' },
      { id: '1', appType: 'custom', pcUrl: '/center/chat' },
      { id: '1', appType: 'external', pcUrl: '/center/chat/ppt?new=true' },
      { id: '1', appType: 'custom', pcUrl: '/center/chat/ppt', h5Url: '/center/chat/campus' },
      { appType: 'custom', pcUrl: '/center/chat/ppt' },
    ]) expect(getManagedBuiltinCatalogId(record)).toBeUndefined();
  });
});
