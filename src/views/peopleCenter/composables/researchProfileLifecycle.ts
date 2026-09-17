export type RunTerminalOutcome = 'completed' | 'partial' | 'failed' | 'cancelled' | null;

/**
 * Research 的 `partial` 是 Completion Verifier 对交付质量的描述：Run 本身已经
 * completed，报告也已经交付，只是仍有非关键证据缺口。它不能被解释为 composer
 * 还要跨轮保持 Research。只有失败或用户停止才保留选择，方便原地重试/继续。
 */
export function researchRunDelivered(outcome: RunTerminalOutcome): boolean {
  return outcome === 'completed' || outcome === 'partial';
}

