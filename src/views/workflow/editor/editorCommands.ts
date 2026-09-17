import type { StoreNodeItemType } from '../core/type';
import { FlowNodeTypeEnum } from '../core/constants';

type PointerPosition = { x: number; y: number };
type ShortcutTargetLike = {
  closest?: (selector: string) => unknown;
};
type WorkflowShortcutOptions = {
  hasWorkflowSelection?: boolean;
};
type ClipboardLike = {
  writeText?: (text: string) => Promise<void>;
  readText?: () => Promise<string>;
};
type TextSelectionTargetLike = {
  selectionStart?: number | null;
  selectionEnd?: number | null;
};
type TextSelectionRootLike = {
  activeElement?: unknown;
  getSelection?: () => { toString: () => string } | null;
};
type NodeElementRootLike = {
  querySelector?: (selector: string) => unknown;
};

export const WORKFLOW_CLIPBOARD_MARK = 'ai-workflow-nodes';
export const WORKFLOW_VUE_FLOW_DELETE_KEY_CODE = null;
let localWorkflowClipboardText = '';

const EDITABLE_SELECTOR = [
  'input',
  'textarea',
  'select',
  '[contenteditable="true"]',
  '[contenteditable="plaintext-only"]',
  '[role="textbox"]',
  '[role="combobox"]',
  '.CodeMirror',
  '.codemirror',
  '.vditor',
  '.tox-tinymce',
].join(', ');

const BLOCKING_POPUP_TARGET_SELECTOR = [
  '.ant-modal',
  '.ant-drawer',
].join(', ');

const TRANSIENT_POPUP_TARGET_SELECTOR = [
  '.ant-popover:not(.ant-popover-hidden)',
  '.ant-dropdown:not(.ant-dropdown-hidden)',
  '.ant-select-dropdown:not(.ant-select-dropdown-hidden)',
  '.ant-cascader-dropdown:not(.ant-cascader-dropdown-hidden)',
  '.ant-picker-dropdown:not(.ant-picker-dropdown-hidden)',
].join(', ');

const OPEN_BLOCKING_POPUP_SELECTOR = [
  '.ant-modal-wrap:not([style*="display: none"])',
  '.ant-drawer-open',
].join(', ');

const OPEN_TRANSIENT_POPUP_SELECTOR = [
  '.ant-popover:not(.ant-popover-hidden)',
  '.ant-dropdown:not(.ant-dropdown-hidden)',
  '.ant-select-dropdown:not(.ant-select-dropdown-hidden)',
  '.ant-cascader-dropdown:not(.ant-cascader-dropdown-hidden)',
  '.ant-picker-dropdown:not(.ant-picker-dropdown-hidden)',
].join(', ');

export function isHandleClick(start: PointerPosition, end: PointerPosition, threshold = 6): boolean {
  return Math.hypot(end.x - start.x, end.y - start.y) <= threshold;
}

export function isWorkflowDeleteShortcut(key: string): boolean {
  return key.toLowerCase() === 'delete';
}

export function shouldIgnoreWorkflowShortcut(
  target: unknown,
  popupRoot?: { querySelector?: (selector: string) => unknown },
  options: WorkflowShortcutOptions = {},
): boolean {
  const element = target as ShortcutTargetLike | null;
  if (element?.closest?.(BLOCKING_POPUP_TARGET_SELECTOR)) return true;
  if (!options.hasWorkflowSelection && element?.closest?.(EDITABLE_SELECTOR)) return true;
  if (!options.hasWorkflowSelection && element?.closest?.(TRANSIENT_POPUP_TARGET_SELECTOR)) return true;

  const root = popupRoot || (typeof document !== 'undefined' ? document : undefined);
  if (root?.querySelector?.(OPEN_BLOCKING_POPUP_SELECTOR)) return true;
  if (!options.hasWorkflowSelection && root?.querySelector?.(OPEN_TRANSIENT_POPUP_SELECTOR)) return true;
  return false;
}

function parseWorkflowClipboardText(text: string) {
  try {
    const payload = JSON.parse(text);
    if (payload?.type === WORKFLOW_CLIPBOARD_MARK && Array.isArray(payload.nodes)) return payload;
  } catch {
    // ignore non-workflow clipboard text
  }
  return undefined;
}

function getBrowserClipboard(): ClipboardLike | undefined {
  return typeof navigator !== 'undefined' ? navigator.clipboard : undefined;
}

export async function writeWorkflowClipboardPayload(
  payload: { nodes: StoreNodeItemType[]; edges?: unknown[] },
  clipboard: ClipboardLike | undefined = getBrowserClipboard(),
): Promise<boolean> {
  const text = JSON.stringify({ type: WORKFLOW_CLIPBOARD_MARK, nodes: payload.nodes, edges: payload.edges || [] });
  localWorkflowClipboardText = text;
  try {
    await clipboard?.writeText?.(text);
    return true;
  } catch {
    return false;
  }
}

export async function readWorkflowClipboardPayload(clipboard: ClipboardLike | undefined = getBrowserClipboard()) {
  try {
    const systemPayload = parseWorkflowClipboardText((await clipboard?.readText?.()) || '');
    if (systemPayload) return systemPayload;
  } catch {
    // fall back to the in-page clipboard below
  }
  return parseWorkflowClipboardText(localWorkflowClipboardText);
}

function hasElementTextSelection(target: unknown): boolean {
  const element = target as TextSelectionTargetLike | null;
  return (
    typeof element?.selectionStart === 'number' &&
    typeof element.selectionEnd === 'number' &&
    element.selectionEnd > element.selectionStart
  );
}

export function hasNativeTextSelection(
  target: unknown,
  root: TextSelectionRootLike | undefined = typeof document !== 'undefined' ? document : undefined,
): boolean {
  if (hasElementTextSelection(target) || hasElementTextSelection(root?.activeElement)) return true;
  const selectionProvider = root?.getSelection || (typeof window !== 'undefined' ? window.getSelection?.bind(window) : undefined);
  const selectedText = selectionProvider?.()?.toString() || '';
  return selectedText.trim().length > 0;
}

function isEditableShortcutTarget(
  target: unknown,
  root: TextSelectionRootLike | undefined = typeof document !== 'undefined' ? document : undefined,
): boolean {
  const element = target as ShortcutTargetLike | null;
  const activeElement = root?.activeElement as ShortcutTargetLike | null;
  return !!element?.closest?.(EDITABLE_SELECTOR) || !!activeElement?.closest?.(EDITABLE_SELECTOR);
}

export function shouldUseNativeTextShortcut(
  key: string,
  target: unknown,
  root: TextSelectionRootLike | undefined = typeof document !== 'undefined' ? document : undefined,
): boolean {
  const normalized = key.toLowerCase();
  if (!['a', 'c', 'v', 'x'].includes(normalized)) return false;
  if (isEditableShortcutTarget(target, root)) return true;
  return (normalized === 'c' || normalized === 'x') && hasNativeTextSelection(target, root);
}

export function getMeasuredNodeElementUpdates(
  nodeIds: string[],
  root: NodeElementRootLike | undefined = typeof document !== 'undefined' ? document : undefined,
): { updates: { id: string; nodeElement: HTMLElement; forceUpdate: boolean }[]; allMeasured: boolean } {
  const updates: { id: string; nodeElement: HTMLElement; forceUpdate: boolean }[] = [];
  let allMeasured = nodeIds.length > 0;
  nodeIds.forEach((id) => {
    const element = root?.querySelector?.(`.vue-flow__node[data-id="${id}"]`) as HTMLElement | undefined;
    if (!element || (element.offsetWidth || 0) <= 0 || (element.offsetHeight || 0) <= 0) {
      allMeasured = false;
      return;
    }
    updates.push({ id, nodeElement: element, forceUpdate: true });
  });
  return { updates, allMeasured };
}

export function collectRemovableNodeIds(
  nodes: StoreNodeItemType[],
  selectedIds: Iterable<string>,
  isProtected: (node: StoreNodeItemType) => boolean,
): Set<string> {
  const nodeById = new Map(nodes.map((node) => [node.nodeId, node]));
  const removable = new Set<string>();
  const pending = [...selectedIds].map((nodeId) => ({ nodeId, cascaded: false }));

  while (pending.length) {
    const { nodeId, cascaded } = pending.pop()!;
    const node = nodeById.get(nodeId);
    if (!node || removable.has(nodeId)) continue;
    if (!cascaded && isProtected(node)) continue;
    removable.add(nodeId);
    nodes.forEach((child) => {
      if (child.parentNodeId === nodeId) pending.push({ nodeId: child.nodeId, cascaded: true });
    });
  }

  return removable;
}

export function isProtectedFromDirectRemoval(
  nodes: StoreNodeItemType[],
  node: StoreNodeItemType,
  isTemplateProtected: (node: StoreNodeItemType) => boolean,
): boolean {
  if (!isTemplateProtected(node)) return false;
  if (node.flowNodeType === FlowNodeTypeEnum.loopRunStart) {
    const parent = nodes.find((item) => item.nodeId === node.parentNodeId);
    return parent?.flowNodeType === FlowNodeTypeEnum.loopRun;
  }
  return true;
}
