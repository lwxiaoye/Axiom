import type { InjectionKey, WritableComputedRef } from 'vue';
import { inject } from 'vue';
import { useAgentMarket, type CenterSectionKey } from './composables/useAgentMarket';
import { useCenterChat } from './composables/useCenterChat';

/**
 * WS1：路由多页共享上下文。
 *
 * 会话/流控/模型等状态实例化于外壳 center.vue（provide），各子页 pages/*Page.vue
 * inject 后渲染对应 tab。状态挂在外壳上，切换子路由时外壳不卸载 → 进行中的流式对话不中断。
 */
export interface CenterContext {
  agentMarket: ReturnType<typeof useAgentMarket>;
  centerChat: ReturnType<typeof useCenterChat>;
  activeSection: WritableComputedRef<CenterSectionKey>;
  showError: (error: unknown) => void;
  showNotice: (message: string) => void;
  switchSection: (section: CenterSectionKey) => void;
  startChatWithAgent: (agent: any) => void;
  openAgentFromChat: (app: any) => void;
}

export const CenterContextKey: InjectionKey<CenterContext> = Symbol('centerContext');

export function useCenterContext(): CenterContext {
  const ctx = inject(CenterContextKey);
  if (!ctx) {
    throw new Error('CenterContext 未提供：子页必须渲染在 center.vue 的 <router-view> 内');
  }
  return ctx;
}
