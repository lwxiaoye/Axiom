import fs from 'node:fs';
import path from 'node:path';

const source = fs.readFileSync(path.resolve(__dirname, 'MessageList.vue'), 'utf8');

describe('MessageList 阅读优先自动跟随契约', () => {
  it('滚动和手势监听共用真实容器解析，不把校园或演示页当成主对话 workspace', () => {
    expect(source).toContain('return resolveMessageScrollContainer(messageListRef.value)');
    expect(source).toContain('resolveMessageScrollContainer(list?.parentElement ?? null)');
    expect(source).toContain("outerScroller?.addEventListener('touchmove', onUserScrollUp");
    expect(source).not.toContain("list.closest('.workspace')");
    expect(source).not.toContain("list?.closest('.workspace')");
  });

  it('生成中展开 Thought 立刻暂停，收起最后一个阅读 Thought 才允许恢复', () => {
    expect(source).toContain('shouldPauseAutoFollowForThought(opening, Boolean(props.loading))');
    expect(source).toContain('thoughtReadingPauseKeys.add(key)');
    expect(source).toContain('thoughtReadingPauseKeys.delete(key) && !hasAutoFollowPause()');
  });

  it('消息增长和 loading 变化都经过统一阅读锁，不可直接按 stickToBottom 抢视口', () => {
    expect(source.match(/canAutoFollow\(stickToBottom\.value, manualReadingPause, thoughtReadingPauseKeys\.size\)/g))
      .toHaveLength(3);
    expect(source).not.toContain('else if (stickToBottom.value) scrollToBottom(false)');
    expect(source).not.toContain('if (loading && stickToBottom.value)');
  });

  it('向上滚立即锁定，只有朝底部滚回阈值内或 force 操作才解除', () => {
    expect(source).toContain('manualReadingPause = true;');
    expect(source).toContain('shouldResumeManualAutoFollow(manualReadingPause, dist, direction, pointerActive)');
    expect(source).toContain('if (force) clearAutoFollowPauses();');
    expect(source).toContain('dist < AUTO_FOLLOW_BUTTON_DISTANCE && !hasAutoFollowPause()');
  });

  it('运行中贴底手势不与流式 scrollTop 互抢', () => {
    expect(source).toContain('shouldSkipProgrammaticStick(pointerActive, dist)');
    expect(source).toContain('window.addEventListener(\'touchend\', onPointerRelease');
    expect(source).toContain('scrollTarget.scrollTop = scrollTarget.scrollHeight');
  });
});
