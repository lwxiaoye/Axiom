/**
 * pickInitialSession 回归（三轮评审 P2b + 二轮 fallback）：悬浮子智能体窗打开时的默认选中，
 * 绝不落到其他主对话的委派会话或孤儿委派会话。sessions 约定按 updated_at 倒序。
 */
import { pickInitialSession } from './pickInitialSession';
import type { RunSession } from './agentRun.api';

function s(partial: Partial<RunSession> & { id: string }): RunSession {
  return { appId: 'app-1', title: '会话', ...partial } as RunSession;
}

test('优先当前主对话的委派会话', () => {
  const sessions = [
    s({ id: 'other-deleg', origin: 'delegation', parentThreadId: 'main-B' }),
    s({ id: 'mine', origin: 'delegation', parentThreadId: 'main-A' }),
    s({ id: 'manual', origin: null, parentThreadId: null }),
  ];
  expect(pickInitialSession(sessions, 'main-A')).toBe('mine');
});

test('无当前主对话委派会话 → 回退最新非委派会话，不落到其他主对话的委派', () => {
  const sessions = [
    s({ id: 'other-deleg', origin: 'delegation', parentThreadId: 'main-B' }),
    s({ id: 'manual', origin: null, parentThreadId: null }),
  ];
  // 本轮委派进行中（本主对话的委派会话尚未落库），只能回退到手动会话，绝不选 other-deleg
  expect(pickInitialSession(sessions, 'main-A')).toBe('manual');
});

test('孤儿委派会话（parent 被清空但 origin 仍在）不被当作非委派回退', () => {
  const sessions = [
    s({ id: 'orphan-deleg', origin: 'delegation', parentThreadId: null }),
    s({ id: 'manual', origin: null, parentThreadId: null }),
  ];
  expect(pickInitialSession(sessions, 'main-A')).toBe('manual');
});

test('全是委派会话（含孤儿）时返回 null → 交调用方新建空对话', () => {
  const sessions = [
    s({ id: 'orphan-deleg', origin: 'delegation', parentThreadId: null }),
    s({ id: 'other-deleg', origin: 'delegation', parentThreadId: 'main-B' }),
  ];
  expect(pickInitialSession(sessions, 'main-A')).toBeNull();
});

test('无 parentThreadId（未从主对话打开）时只按 origin 取最新非委派会话', () => {
  const sessions = [
    s({ id: 'deleg', origin: 'delegation', parentThreadId: 'main-B' }),
    s({ id: 'manual', origin: null, parentThreadId: null }),
  ];
  expect(pickInitialSession(sessions, undefined)).toBe('manual');
});

test('空列表返回 null', () => {
  expect(pickInitialSession([], 'main-A')).toBeNull();
});
