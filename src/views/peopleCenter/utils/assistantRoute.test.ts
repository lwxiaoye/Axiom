import { openBuiltinAssistantPage, toInternalPath } from './assistantRoute';

describe('toInternalPath', () => {
  const origin = 'http://localhost:3200';

  it('站内路径与同源绝对地址都归一成站内路径（保留 query/hash）', () => {
    expect(toInternalPath('/center/chat/campus', origin)).toBe('/center/chat/campus');
    expect(toInternalPath(`${origin}/center/chat/interview?thread=t1#top`, origin))
      .toBe('/center/chat/interview?thread=t1#top');
  });

  it('外部地址与空地址返回 null', () => {
    expect(toInternalPath('https://ext.example/app?x=1', origin)).toBeNull();
    expect(toInternalPath('', origin)).toBeNull();
  });
});

describe('openBuiltinAssistantPage', () => {
  it('同源地址在当前标签页站内跳转', () => {
    // 新标签页没有历史，助手页里的「返回」就无处可回——同源一律站内跳转。
    const opened: Array<{ url: string; name: string }> = [];
    const navigated: string[] = [];
    const ok = openBuiltinAssistantPage(
      '/center/chat/ppt?thread=abc',
      (url, name) => {
        opened.push({ url, name });
        return {} as Window;
      },
      (path) => navigated.push(path),
    );
    expect(navigated).toEqual(['/center/chat/ppt?thread=abc']);
    expect(opened).toEqual([]);
    expect(ok).toBe(true);
  });

  it('外部地址退回新开标签页并断开 opener', () => {
    const opened: Array<{ url: string; name: string }> = [];
    const navigated: string[] = [];
    const fake = { opener: {} as Window | null };
    const ok = openBuiltinAssistantPage(
      'https://ext.example/app?x=1',
      (url, name) => {
        opened.push({ url, name });
        return fake as unknown as Window;
      },
      (path) => navigated.push(path),
    );
    expect(opened).toEqual([{ url: 'https://ext.example/app?x=1', name: '_blank' }]);
    expect(navigated).toEqual([]);
    expect(fake.opener).toBeNull();
    expect(ok).toBe(true);
  });

  it('没有可打开的地址时报告失败', () => {
    expect(openBuiltinAssistantPage('', () => null, () => undefined)).toBe(false);
  });
});
