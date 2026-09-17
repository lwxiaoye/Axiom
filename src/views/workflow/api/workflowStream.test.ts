import { dispatchWorkflowStreamEvent } from './workflowStream';

describe('workflow stream parser', () => {
  it('dispatches delta events before the final result', () => {
    const deltas: string[] = [];
    const results: string[] = [];

    dispatchWorkflowStreamEvent('event: delta\ndata: {"text":"你"}', {
      onDelta: (text) => deltas.push(text),
      onResult: (result) => results.push(result.output),
    });
    dispatchWorkflowStreamEvent('event: delta\ndata: {"text":"好"}', {
      onDelta: (text) => deltas.push(text),
      onResult: (result) => results.push(result.output),
    });
    dispatchWorkflowStreamEvent('event: result\ndata: {"runId":"r1","status":"success","output":"你好","durationMs":1,"nodeRuns":[]}', {
      onDelta: (text) => deltas.push(text),
      onResult: (result) => results.push(result.output),
    });

    expect(deltas).toEqual(['你', '好']);
    expect(results).toEqual(['你好']);
  });
});
