import {
  AGENT_CAPABILITY_DEFINITIONS,
  type AgentCapabilityKey,
} from '@/views/peopleCenter/agentMarketCapabilities';

/** 系统设置应用表单与智能体广场共用同一能力分类事实源。 */
export const APP_CAPABILITY_OPTIONS = AGENT_CAPABILITY_DEFINITIONS.map(({ key, label }) => ({
  value: key,
  label,
}));

const APP_CAPABILITY_VALUES = new Set<string>(
  APP_CAPABILITY_OPTIONS.map((option) => option.value),
);

export function isAppCapabilityValue(value: unknown): value is AgentCapabilityKey {
  return APP_CAPABILITY_VALUES.has(String(value || '').trim());
}
