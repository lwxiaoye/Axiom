import {
  absorbFailedRunIfUserClarification,
  isUserClarificationMessage,
  isUserClarificationText,
} from './userClarification';

const WESTBROOK_ASK = [
  '好。在正式开始系统检索前，我想确认一个会直接影响研究方向的问题：',
  '你想研究威少生涯数据时，是否需要与特定球员做横向对比',
  '（比如和保罗、哈登、艾弗森、韦德等同代/历史后卫对比），',
  '还是纯粹聚焦威少本人的生涯数据梳理？',
].join('');

describe('isUserClarificationText', () => {
  it('recognizes a Deep Research scope question', () => {
    expect(isUserClarificationText(WESTBROOK_ASK)).toBe(true);
  });

  it('rejects a finished report even if the last sentence has a question mark', () => {
    const report = `${'威少生涯数据综述。'.repeat(80)}最后，这是否意味着他的影响力被低估？`;
    expect(isUserClarificationText(report)).toBe(false);
  });

  it('rejects a plain answer without a question', () => {
    expect(isUserClarificationText('威少生涯场均约 22 分 7 篮板 8 助攻，三双次数联盟前列。')).toBe(false);
  });
});

describe('isUserClarificationMessage', () => {
  it('is false once a report file already exists', () => {
    expect(isUserClarificationMessage({
      content: WESTBROOK_ASK,
      generatedFiles: [{ filename: '威少生涯.html' }],
    })).toBe(false);
  });

  it('ignores companion .research.md when judging products', () => {
    expect(isUserClarificationMessage({
      content: WESTBROOK_ASK,
      generatedFiles: [{ filename: '威少生涯.research.md' }],
    })).toBe(true);
  });
});

describe('absorbFailedRunIfUserClarification', () => {
  it('clears the error and completes instead of failing', () => {
    const target = { content: WESTBROOK_ASK, error: '任务执行失败，请稍后重试' };
    const complete = jest.fn((item: { error?: string }) => {
      item.error = undefined;
    });
    expect(absorbFailedRunIfUserClarification(target, complete)).toBe(true);
    expect(complete).toHaveBeenCalledTimes(1);
    expect(target.error).toBeUndefined();
  });

  it('leaves real failures alone', () => {
    const target = { content: '检索中断。', error: '任务执行失败，请稍后重试' };
    const complete = jest.fn();
    expect(absorbFailedRunIfUserClarification(target, complete)).toBe(false);
    expect(complete).not.toHaveBeenCalled();
    expect(target.error).toBe('任务执行失败，请稍后重试');
  });
});
