import { defHttp } from '/@/utils/http/axios';

export type WorkbenchOverview = {
  metrics: Record<string, { value: number; growth: number }>;
  capabilities: Record<string, number>;
  usage: { total: number; today: number; trend: Array<{ date: string; value: number }> };
  knowledge: { chunks: number; successRate: number; recallRate: number; averageLatencyMs: number; citationRate: number };
};

export const getWorkbenchOverview = (period: string) =>
  defHttp.get<WorkbenchOverview>({ url: '/dashboard/workbench/overview', params: { period } }, { errorMessageMode: 'none' });
