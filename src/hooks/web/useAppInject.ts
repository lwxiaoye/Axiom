// 同 useDesign：从叶子模块取 useAppProviderContext，绕开 components/Application 桶文件，
// 否则 AppSearchModal.vue → useAppInject → index.ts → AppSearch.vue → AppSearchModal.vue 成环。
import { useAppProviderContext } from '/@/components/Application/src/useAppContext';
import { computed, unref } from 'vue';

export function useAppInject() {
  const values = useAppProviderContext();

  return {
    getIsMobile: computed(() => unref(values.isMobile)),
  };
}
