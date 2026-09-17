import { ref } from 'vue';
import { createSessionLiveRuns } from './sessionLiveRuns';

describe('createSessionLiveRuns', () => {
  it('当前会话在跑才算 running，新对话视图不算', () => {
    const active = ref('s1');
    const live = createSessionLiveRuns(active);
    live.start('s1', {
      abort: new AbortController(),
      messages: [],
      nodeLabel: '',
      view: 0,
      interactive: null,
    });
    expect(live.running.value).toBe(true);
    active.value = '';
    expect(live.running.value).toBe(false);
    expect(live.liveIds.value).toEqual(['s1']);
  });

  it('空会话启动中：同视图 busy，切到下一视图后不 busy', () => {
    const active = ref('');
    const live = createSessionLiveRuns(active);
    const view = live.view.value;
    live.markStarting(view);
    expect(live.isBusy('', view)).toBe(true);
    expect(live.running.value).toBe(true);
    live.nextView();
    expect(live.running.value).toBe(false);
    expect(live.isBusy('', live.view.value)).toBe(false);
    live.clearStarting(view);
  });
});
