import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

/**
 * 应用壳循环依赖守卫。
 *
 * 背景：`hooks/web/useDesign` / `useAppInject` 曾经从 `components/Application` 桶文件取
 * `useAppProviderContext`，而桶文件把 AppLogo/AppSearch/AppDarkModeToggle 等组件全部拉进来，
 * 它们又各自 import useDesign——17 个模块的循环依赖簇。dev 下 ESM 按首次进入的模块决定
 * 求值顺序，首屏偶发 `ReferenceError: Cannot access 'appLogo' before initialization`，
 * loading 遮罩永不消失。修法是从叶子模块 `Application/src/useAppContext` 直接取。
 *
 * 这里只锁两件事：① 两个 hook 不再回指桶文件；② 叶子模块保持无内部依赖（只允许 vue 与
 * hooks/core/useContext），否则谁往里加一个 `/@/components/...` 的 import，环就悄悄回来了。
 * 放在 peopleCenter 下是因为只有 chat 这套 jest 配置会跑到；它守的是全局应用壳。
 */
const src = resolve(__dirname, '../..');
const read = (rel: string) => readFileSync(resolve(src, rel), 'utf8');

const importSpecs = (code: string): string[] =>
  Array.from(code.matchAll(/^\s*(?:import|export)\s+(?!type\s)[^;'"]*?from\s+['"]([^'"]+)['"]/gm)).map((m) => m[1]);

describe('应用壳循环依赖守卫（appLogo TDZ）', () => {
  it('useDesign / useAppInject 从叶子模块取 useAppProviderContext，不再经 components/Application 桶文件', () => {
    for (const file of ['hooks/web/useDesign.ts', 'hooks/web/useAppInject.ts']) {
      const specs = importSpecs(read(file));
      expect(specs).toContain('/@/components/Application/src/useAppContext');
      expect(specs).not.toContain('/@/components/Application');
      expect(specs).not.toContain('@/components/Application');
    }
  });

  it('useAppContext 保持叶子：只依赖 vue 与 hooks/core/useContext', () => {
    const specs = importSpecs(read('components/Application/src/useAppContext.ts'));
    expect(new Set(specs)).toEqual(new Set(['vue', '/@/hooks/core/useContext']));
    const ctxSpecs = importSpecs(read('hooks/core/useContext.ts'));
    expect(ctxSpecs).toEqual(['vue']);
  });

  it('桶文件仍对外导出 useAppProviderContext（公开 API 不变）', () => {
    expect(read('components/Application/index.ts')).toContain(
      "export { useAppProviderContext } from './src/useAppContext';",
    );
  });
});
