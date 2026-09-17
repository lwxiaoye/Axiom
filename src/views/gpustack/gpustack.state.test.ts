import {
  canSubmitDeployment,
  compatibilityFromEvaluation,
  liveResourceCounts,
  modelProgressFor,
  nextFreshness,
  normalizeGpuStackError,
} from './gpustack.state';

describe('GPUStack view state', () => {
  it('uses only instances belonging to the requested model for deployment progress', () => {
    expect(
      modelProgressFor(7, 2, 1, [
        { model_id: 7, download_progress: 0.4 },
        { model_id: 9, download_progress: 0.9 },
      ]),
    ).toBe(40);
  });

  it('returns zero progress for stopped deployments', () => {
    expect(modelProgressFor(7, 0, 0, [])).toBe(0);
  });

  it('keeps prior data stale when a refresh fails', () => {
    expect(nextFreshness(true, false)).toBe('stale');
  });

  it('marks an initial refresh failure as failed', () => {
    expect(nextFreshness(false, false)).toBe('failed');
  });

  it('blocks incompatible deployment and requires acknowledgement for unknown compatibility', () => {
    expect(canSubmitDeployment('incompatible', false)).toBe(false);
    expect(canSubmitDeployment('unknown', false)).toBe(false);
    expect(canSubmitDeployment('unknown', true)).toBe(true);
  });

  it('reads the structured upstream error returned by the backend', () => {
    expect(normalizeGpuStackError({ result: { category: 'LOG_PROXY', detail: 'worker unavailable' } }, 'Fallback')).toEqual({
      category: 'log_proxy',
      message: 'worker unavailable',
    });
  });

  it('derives compatibility from all evaluated deployment specs', () => {
    expect(compatibilityFromEvaluation([{ compatible: true }])).toBe('compatible');
    expect(compatibilityFromEvaluation([{ compatible: true }, { compatible: false }])).toBe('incompatible');
    expect(compatibilityFromEvaluation([])).toBe('unknown');
  });

  it('uses live GPU inventory when dashboard resource counts are empty', () => {
    expect(
      liveResourceCounts(
        { worker_count: 0, gpu_count: 0, model_count: 0, model_instance_count: 0 },
        { workerCount: 0, gpuCount: 0, modelCount: 0 },
        [{ worker_id: 1 }, { worker_id: 1 }, { worker_id: 2 }],
      ),
    ).toEqual({ workerCount: 2, gpuCount: 3, modelCount: 0, instanceCount: 0 });
  });
});
