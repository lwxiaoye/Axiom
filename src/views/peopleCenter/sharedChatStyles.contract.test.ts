import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { compileStyleAsync, parse } from '@vue/compiler-sfc';

describe('主对话与内置应用的共享样式编译', () => {
  it.each([
    ['center.vue', './styles/centerNew.less'],
    ['pages/BuiltinHarnessRunPage.vue', '../styles/centerNew.less'],
  ])('%s 保留自己的样式描述符并复用全局 Less', async (page, sharedImport) => {
    const filename = resolve(__dirname, page);
    const { descriptor, errors } = parse(readFileSync(filename, 'utf8'), { filename });
    expect(errors).toEqual([]);
    // Unscoped style src is cached by the shared filename, not its owning SFC.
    // Different style indices across these pages used to fail with undefined.scoped.
    expect(descriptor.styles.some((style) => style.src?.endsWith('centerNew.less'))).toBe(false);
    const sharedStyles = descriptor.styles.filter((style) => style.content.includes(sharedImport));
    expect(sharedStyles).toHaveLength(1);
    const style = sharedStyles[0];
    expect(style.lang).toBe('less');
    expect(style.scoped).toBeFalsy();
    const compiled = await compileStyleAsync({
      filename,
      source: style.content,
      id: 'data-v-shared-chat-test',
      scoped: false,
      preprocessLang: 'less',
    });
    expect(compiled.errors).toEqual([]);
    expect(compiled.code).toContain('.chat-home');
    expect(compiled.code).toContain('.history-popover');
    expect(compiled.code).not.toContain('[data-v-shared-chat-test]');
  });
});
