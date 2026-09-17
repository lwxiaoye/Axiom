import { reactive } from 'vue';
import type { WorkflowChartOutput } from '../shared/chartOutput';

export type DebugMessage = {
  role: 'user' | 'assistant';
  content: string;
  failed?: boolean;
  meta?: string;
  chartOutputs?: WorkflowChartOutput[];
};

export function createDebugMessage(role: DebugMessage['role'] = 'assistant'): DebugMessage {
  return reactive<DebugMessage>({ role, content: '' });
}
