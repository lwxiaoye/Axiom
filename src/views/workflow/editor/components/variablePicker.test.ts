import { flattenVariableGroups, formatVariableTemplate, nextVariablePickerIndex } from './variablePicker';

describe('variable picker keyboard navigation', () => {
  it('wraps the active option for arrow navigation', () => {
    expect(nextVariablePickerIndex(0, 3, 'up')).toBe(2);
    expect(nextVariablePickerIndex(2, 3, 'down')).toBe(0);
  });

  it('keeps an empty picker unselected', () => {
    expect(nextVariablePickerIndex(-1, 0, 'down')).toBe(-1);
  });

  it('wraps inserted variables in template braces exactly once', () => {
    expect(formatVariableTemplate('input.name')).toBe('{{input.name}}');
    expect(formatVariableTemplate('{{input.name}}')).toBe('{{input.name}}');
  });

  it('flattens grouped options for the existing arrow and enter navigation', () => {
    expect(
      flattenVariableGroups([
        { label: '节点变量', options: [{ label: '开始 / 问题', detail: 'userChatInput', value: 'node:start:userChatInput' }] },
        { label: '全局变量', options: [{ label: '用户姓名', detail: 'realname', value: 'realname' }] },
      ])
    ).toEqual([
      { label: '开始 / 问题', detail: 'userChatInput', value: 'node:start:userChatInput' },
      { label: '用户姓名', detail: 'realname', value: 'realname' },
    ]);
  });
});
