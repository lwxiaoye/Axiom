export type DataFreshness = 'fresh' | 'stale' | 'failed';

export type CompatibilityState = 'compatible' | 'incompatible' | 'unknown';

export interface ModelInstanceSummary {
  id?: number;
  model_id?: number;
  download_progress?: number | null;
}

export interface GpuStackViewError {
  category: 'validation' | 'authentication' | 'connectivity' | 'capacity_or_scheduling' | 'runtime' | 'log_proxy';
  message: string;
}
