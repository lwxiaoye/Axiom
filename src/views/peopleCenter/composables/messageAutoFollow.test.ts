import {
  AUTO_FOLLOW_RESUME_DISTANCE,
  canAutoFollow,
  hasAutoFollowReadingPause,
  scrollDirection,
  shouldPauseAutoFollowForThought,
  shouldResumeManualAutoFollow,
  shouldSkipProgrammaticStick,
} from './messageAutoFollow';

describe('主对话自动跟随阅读锁', () => {
  it('生成中展开 Thought 会暂停，完成后查看历史 Thought 不额外锁住列表', () => {
    expect(shouldPauseAutoFollowForThought(true, true)).toBe(true);
    expect(shouldPauseAutoFollowForThought(true, false)).toBe(false);
    expect(shouldPauseAutoFollowForThought(false, true)).toBe(false);
  });

  it('手动阅读和任一展开 Thought 都优先于贴底状态', () => {
    expect(hasAutoFollowReadingPause(true, 0)).toBe(true);
    expect(hasAutoFollowReadingPause(false, 2)).toBe(true);
    expect(canAutoFollow(true, true, 0)).toBe(false);
    expect(canAutoFollow(true, false, 1)).toBe(false);
    expect(canAutoFollow(true, false, 0)).toBe(true);
  });

  it('向上滚第一帧保持暂停，不会因仍位于 40px 阈值内立即恢复', () => {
    const direction = scrollDirection(980, 1000);
    expect(direction).toBe('away');
    expect(shouldResumeManualAutoFollow(true, AUTO_FOLLOW_RESUME_DISTANCE - 1, direction)).toBe(false);
  });

  it('只有用户主动向下回到底部才解除手动阅读锁', () => {
    expect(scrollDirection(1001, 1000)).toBe('toward');
    expect(scrollDirection(1000.2, 1000)).toBe('stationary');
    expect(shouldResumeManualAutoFollow(true, AUTO_FOLLOW_RESUME_DISTANCE - 1, 'toward')).toBe(true);
    expect(shouldResumeManualAutoFollow(true, AUTO_FOLLOW_RESUME_DISTANCE, 'toward')).toBe(false);
    expect(shouldResumeManualAutoFollow(true, 0, 'stationary')).toBe(false);
  });

  it('贴底过拉或手指未离开时不恢复跟随、也不程序化写 scrollTop', () => {
    expect(shouldResumeManualAutoFollow(true, -8, 'toward')).toBe(false);
    expect(shouldResumeManualAutoFollow(true, 8, 'toward', true)).toBe(false);
    expect(shouldSkipProgrammaticStick(true, 24)).toBe(true);
    expect(shouldSkipProgrammaticStick(false, 0)).toBe(true);
    expect(shouldSkipProgrammaticStick(false, 12)).toBe(false);
  });
});
