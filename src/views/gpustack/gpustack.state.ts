import type { CompatibilityState, DataFreshness, GpuStackViewError, ModelInstanceSummary } from './gpustack.types';

const errorCategories: GpuStackViewError['category'][] = [
  'validation',
  'authentication',
  'connectivity',
  'capacity_or_scheduling',
  'runtime',
  'log_proxy',
];

export function nextFreshness(hasPriorData: boolean, requestSucceeded: boolean): DataFreshness {
  if (requestSucceeded) return 'fresh';
  return hasPriorData ? 'stale' : 'failed';
}

export function modelProgressFor(
  modelId: number,
  replicas: number,
  readyReplicas: number,
  instances: ModelInstanceSummary[],
): number {
  if (replicas <= 0) return 0;

  const downloading = instances
    .filter((instance) => instance.model_id === modelId && instance.download_progress != null)
    .map((instance) => Number(instance.download_progress) * 100);

  if (downloading.length) return Math.min(99, Math.round(Math.max(...downloading)));
  return Math.min(99, Math.round((readyReplicas / replicas) * 100));
}

export function canSubmitDeployment(state: CompatibilityState, acknowledgedUnknown: boolean): boolean {
  return state === 'compatible' || (state === 'unknown' && acknowledgedUnknown);
}

export function compatibilityFromEvaluation(results: Array<{ compatible?: unknown }>): CompatibilityState {
  if (!results.length) return 'unknown';
  if (results.some((result) => result.compatible === false)) return 'incompatible';
  if (results.every((result) => result.compatible === true)) return 'compatible';
  return 'unknown';
}

type DashboardResourceCounts = {
  worker_count?: unknown;
  gpu_count?: unknown;
  model_count?: unknown;
  model_instance_count?: unknown;
};

type SyncResourceCounts = {
  workerCount?: unknown;
  gpuCount?: unknown;
  modelCount?: unknown;
};

export function liveResourceCounts(
  dashboard: DashboardResourceCounts,
  sync: SyncResourceCounts,
  gpuDevices: Array<{ worker_id?: unknown }>,
) {
  const liveWorkerCount = new Set(gpuDevices.map((device) => device.worker_id).filter((id) => id != null)).size;

  return {
    workerCount: firstPositive(dashboard.worker_count, liveWorkerCount, sync.workerCount),
    gpuCount: firstPositive(dashboard.gpu_count, gpuDevices.length, sync.gpuCount),
    modelCount: firstPositive(dashboard.model_count, sync.modelCount),
    instanceCount: firstPositive(dashboard.model_instance_count),
  };
}

function firstPositive(...values: unknown[]): number {
  return values.map((value) => Number(value) || 0).find((value) => value > 0) ?? 0;
}

export function normalizeGpuStackError(payload: unknown, fallback: string): GpuStackViewError {
  const source = payload as {
    message?: unknown;
    result?: { category?: unknown; detail?: unknown };
    response?: { data?: { message?: unknown; result?: { category?: unknown; detail?: unknown } } };
  };
  const data = source?.response?.data ?? source;
  const category = String(data?.result?.category || '').toLowerCase();
  const message = data?.result?.detail || data?.message || source?.message;

  return {
    category: errorCategories.includes(category as GpuStackViewError['category'])
      ? (category as GpuStackViewError['category'])
      : 'runtime',
    message: typeof message === 'string' && message.trim() ? message : fallback,
  };
}
