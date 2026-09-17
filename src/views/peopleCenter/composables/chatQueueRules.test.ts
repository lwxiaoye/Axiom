import {
  canInstructQueueItem,
  CANCEL_INTENT_RE,
  moveQueueEntryToIndex,
  reorderQueueByIds,
  resolveFollowUpIntent,
} from './chatQueueRules';

describe('chatQueueRules', () => {
  it('resolveFollowUpIntent 取反', () => {
    expect(resolveFollowUpIntent('steer')).toBe('steer');
    expect(resolveFollowUpIntent('queue', true)).toBe('steer');
    expect(resolveFollowUpIntent('steer', true)).toBe('queue');
  });

  it('CANCEL_INTENT_RE', () => {
    expect(CANCEL_INTENT_RE.test('取消')).toBe(true);
    expect(CANCEL_INTENT_RE.test('算了')).toBe(true);
    expect(CANCEL_INTENT_RE.test('请继续做')).toBe(false);
  });

  it('canInstructQueueItem', () => {
    expect(canInstructQueueItem('pending')).toBe(true);
    expect(canInstructQueueItem('dispatching')).toBe(false);
  });

  it('reorderQueueByIds', () => {
    const cur = [{ id: 'a' }, { id: 'b' }, { id: 'c' }];
    expect(reorderQueueByIds(cur, ['c', 'a', 'b'])?.map((x) => x.id)).toEqual(['c', 'a', 'b']);
    expect(reorderQueueByIds(cur, ['a', 'b'])).toBeNull();
    expect(reorderQueueByIds(cur, ['a', 'b', 'c'])).toBe(cur);
  });

  it('moveQueueEntryToIndex 按移除后的插入位置重排', () => {
    const cur = [{ id: 'a' }, { id: 'b' }, { id: 'c' }];
    expect(moveQueueEntryToIndex(cur, 'a', 1).map((item) => item.id)).toEqual(['b', 'a', 'c']);
    expect(moveQueueEntryToIndex(cur, 'c', 0).map((item) => item.id)).toEqual(['c', 'a', 'b']);
    expect(moveQueueEntryToIndex(cur, 'b', 99).map((item) => item.id)).toEqual(['a', 'c', 'b']);
    expect(moveQueueEntryToIndex(cur, 'missing', 0)).toBe(cur);
    expect(moveQueueEntryToIndex(cur, 'b', 1)).toBe(cur);
  });
});
