// 直接从叶子模块 useAppContext 取，不经 components/Application 桶文件：
// 桶文件 index.ts 会把 AppLogo/AppSearch/AppDarkModeToggle 等一整组组件拉进来，而它们自己
// 又都 import 本文件（useDesign），形成 17 个模块的循环依赖簇。dev 下 ESM 按首次进入的模块
// 决定求值顺序，首屏偶发 `Cannot access 'appLogo' before initialization`（index.ts 的
// `withInstall(appLogo)` 在 AppLogo.vue 求值完成前跑到），loading 遮罩永不消失。
// useAppContext 只依赖 vue 与 hooks/core/useContext，从它取同一个函数，环即断开，行为不变。
import { useAppProviderContext } from '/@/components/Application/src/useAppContext';
// import { computed } from 'vue';
// import { lowerFirst } from 'lodash-es';
export function useDesign(scope: string) {
  const values = useAppProviderContext();
  // const $style = cssModule ? useCssModule() : {};

  // const style: Record<string, string> = {};
  // if (cssModule) {
  //   Object.keys($style).forEach((key) => {
  //     // const moduleCls = $style[key];
  //     const k = key.replace(new RegExp(`^${values.prefixCls}-?`, 'ig'), '');
  //     style[lowerFirst(k)] = $style[key];
  //   });
  // }
  return {
    // prefixCls: computed(() => `${values.prefixCls}-${scope}`),
    prefixCls: `${values.prefixCls}-${scope}`,
    prefixVar: values.prefixCls,
    // style,
  };
}
