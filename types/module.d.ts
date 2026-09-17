declare module '*.vue' {
  import { DefineComponent } from 'vue';
  const Component: DefineComponent<{}, {}, any>;
  export default Component;
}

declare module 'ant-design-vue/es/locale/*' {
  import { Locale } from 'ant-design-vue/types/locale-provider';
  const locale: Locale & ReadonlyRecordable;
  export default locale as Locale & ReadonlyRecordable;
}

declare module 'virtual:*' {
  const result: any;
  export default result;
}

/* @vue-office 的 lib/index.js 由 postinstall 按 vue 版本生成（pnpm 默认拦截构建脚本），
   故直接深路径引 v3 构建；这里补上深路径的组件类型。 */
declare module '@vue-office/docx/lib/v3/vue-office-docx.mjs' {
  import { DefineComponent } from 'vue';
  const Component: DefineComponent<{ src: string | ArrayBuffer }, {}, any>;
  export default Component;
}

declare module '@vue-office/excel/lib/v3/vue-office-excel.mjs' {
  import { DefineComponent } from 'vue';
  const Component: DefineComponent<{ src: string | ArrayBuffer }, {}, any>;
  export default Component;
}

declare module '@vue-office/pptx/lib/v3/vue-office-pptx.mjs' {
  import { DefineComponent } from 'vue';
  const Component: DefineComponent<{ src: string | ArrayBuffer }, {}, any>;
  export default Component;
}

declare module '@vue-office/docx/lib/v3/index.css';
declare module '@vue-office/excel/lib/v3/index.css';

declare module 'virtual:pwa-register/vue' {
  import type { Ref } from 'vue';

  export interface RegisterSWOptions {
    immediate?: boolean;
    onNeedRefresh?: () => void;
    onOfflineReady?: () => void;
    onRegistered?: (registration: ServiceWorkerRegistration | undefined) => void;
    onRegisterError?: (error: any) => void;
  }

  export function useRegisterSW(options?: RegisterSWOptions): {
    needRefresh: Ref<boolean>;
    offlineReady: Ref<boolean>;
    updateServiceWorker: (reloadPage?: boolean) => Promise<void>;
  };
}

