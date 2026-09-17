/** @jest-environment jsdom */

import { resolveMessageScrollContainer } from './messageAutoFollow';

function setBox(element: HTMLElement, height: number, scrollHeight: number) {
  Object.defineProperties(element, {
    clientHeight: { configurable: true, value: height },
    scrollHeight: { configurable: true, value: scrollHeight },
  });
}

function fixture(shellClass = 'builtin-harness-main') {
  const shell = document.createElement('main');
  shell.className = shellClass;
  shell.style.overflowY = 'auto';
  const content = document.createElement('section');
  content.style.overflowY = 'visible';
  const list = document.createElement('div');
  list.className = 'message-list';
  list.style.overflowY = 'visible';
  content.appendChild(list);
  shell.appendChild(content);
  document.body.appendChild(shell);
  setBox(shell, 788, 1512);
  setBox(list, 1336, 1336);
  return { shell, content, list };
}

afterEach(() => { document.body.replaceChildren(); });

test('手机内置应用没有 workspace 时仍找到真实滚动外壳', () => {
  const { shell, list } = fixture();
  expect(list.closest('.workspace')).toBeNull();
  expect(resolveMessageScrollContainer(list)).toBe(shell);
  expect(resolveMessageScrollContainer(list.parentElement)).toBe(shell);
});

test('主对话 workspace 仍使用同一解析逻辑', () => {
  const { shell, list } = fixture('workspace');
  expect(resolveMessageScrollContainer(list)).toBe(shell);
});

test('列表自身确实可滚时仍优先列表，外层手势监听仍找到外壳', () => {
  const { shell, list } = fixture();
  list.style.overflowY = 'auto';
  setBox(list, 400, 1336);
  expect(resolveMessageScrollContainer(list)).toBe(list);
  expect(resolveMessageScrollContainer(list.parentElement)).toBe(shell);
});

test('overflow visible 的内容溢出不是滚动区，不能选中它', () => {
  const { shell, list } = fixture();
  setBox(list, 400, 1336);
  expect(resolveMessageScrollContainer(list)).toBe(shell);
});

test('子容器声明 auto 但没有滚动时，优先真正已溢出的外壳', () => {
  const { shell, content, list } = fixture();
  content.style.overflowY = 'auto';
  setBox(content, 1512, 1512);
  expect(resolveMessageScrollContainer(list)).toBe(shell);
});

test('首句尚未撑满页面时仍返回外壳，避免漏绑后续滚动监听', () => {
  const { shell, list } = fixture();
  setBox(shell, 788, 788);
  expect(resolveMessageScrollContainer(list)).toBe(shell);
});

test('裁剪容器不会替代真实的滚动区', () => {
  const { shell, content, list } = fixture();
  content.style.overflowY = 'hidden';
  setBox(content, 400, 1336);
  expect(resolveMessageScrollContainer(list)).toBe(shell);
});

test('组件未挂载时不访问页面状态', () => {
  expect(resolveMessageScrollContainer(null)).toBeNull();
});
