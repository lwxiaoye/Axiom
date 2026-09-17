import { PageEnum } from '../enums/pageEnum';
import {
  isSafeInternalRedirect,
  loginRedirectQuery,
  resolvePostLoginPath,
  unwrapLoginRedirect,
} from './postLoginRedirect';

describe('resolvePostLoginPath', () => {
  it('uses an internal redirect from the login query', () => {
    expect(resolvePostLoginPath('/center/chat', '/center/chat/campus')).toBe('/center/chat');
  });

  it('unwraps nested /login?redirect= chains from the address bar', () => {
    expect(unwrapLoginRedirect('/login?redirect=/login?redirect=/login?redirect=/center/chat')).toBe(
      '/center/chat',
    );
    expect(
      resolvePostLoginPath(
        '/login?redirect=/login?redirect=/login?redirect=/center/chat',
        '/center/chat/campus',
      ),
    ).toBe('/center/chat');
    expect(resolvePostLoginPath('/login?redirect=/center/chat', '/center/chat/campus')).toBe(
      '/center/chat',
    );
  });

  it('falls back to homePath when redirect is missing or unsafe', () => {
    expect(resolvePostLoginPath(undefined, '/center/chat/campus')).toBe('/center/chat/campus');
    expect(resolvePostLoginPath('/', '/center/chat/campus')).toBe('/center/chat/campus');
    expect(resolvePostLoginPath('//center/chat', '/center/chat/campus')).toBe('/center/chat/campus');
    expect(resolvePostLoginPath('/login', '/center/chat/campus')).toBe('/center/chat/campus');
  });

  it('does not concatenate history.base=/ with a path into a protocol-relative URL', () => {
    const base = '/';
    const redirect = '/center/chat';
    expect(`${base}${redirect}`).toBe('//center/chat');
    expect(isSafeInternalRedirect(`${base}${redirect}`)).toBe(false);
    expect(resolvePostLoginPath(redirect, PageEnum.BASE_HOME)).toBe('/center/chat');
  });

  it('rejects external and login-loop targets', () => {
    expect(isSafeInternalRedirect('https://evil.example/')).toBe(false);
    expect(isSafeInternalRedirect('/login')).toBe(false);
    expect(isSafeInternalRedirect(PageEnum.BASE_HOME)).toBe(true);
    expect(loginRedirectQuery('/login?redirect=/login?redirect=/center/chat')).toEqual({
      redirect: '/center/chat',
    });
    expect(loginRedirectQuery('/login')).toEqual({});
  });
});
