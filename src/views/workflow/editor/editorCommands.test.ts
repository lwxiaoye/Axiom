import type { StoreNodeItemType } from '../core/type';
import { FlowNodeTypeEnum } from '../core/constants';
import {
  collectRemovableNodeIds,
  WORKFLOW_VUE_FLOW_DELETE_KEY_CODE,
  isHandleClick,
  isProtectedFromDirectRemoval,
  hasNativeTextSelection,
  getMeasuredNodeElementUpdates,
  readWorkflowClipboardPayload,
  isWorkflowDeleteShortcut,
  shouldIgnoreWorkflowShortcut,
  shouldUseNativeTextShortcut,
  writeWorkflowClipboardPayload,
} from './editorCommands';

function node(nodeId: string, parentNodeId?: string, flowNodeType = 'test'): StoreNodeItemType {
  return {
    nodeId,
    name: nodeId,
    flowNodeType,
    position: { x: 0, y: 0 },
    inputs: [],
    outputs: [],
    ...(parentNodeId ? { parentNodeId } : {}),
  } as StoreNodeItemType;
}

describe('workflow editor commands', () => {
  it('removes selected editable nodes and all nested children', () => {
    const nodes = [node('start'), node('loop'), node('loop-start', 'loop'), node('break', 'loop')];

    const removed = collectRemovableNodeIds(nodes, ['start', 'loop'], (item) =>
      ['start', 'loop-start'].includes(item.nodeId),
    );

    expect([...removed].sort()).toEqual(['break', 'loop', 'loop-start']);
  });

  it('does not remove a protected node when selected directly', () => {
    const nodes = [node('loop'), node('loop-start', 'loop')];

    const removed = collectRemovableNodeIds(nodes, ['loop-start'], (item) => item.nodeId === 'loop-start');

    expect([...removed]).toEqual([]);
  });

  it('protects loop start only while it belongs to a loop container', () => {
    const loop = node('loop', undefined, FlowNodeTypeEnum.loopRun);
    const loopStart = node('loop-start', 'loop', FlowNodeTypeEnum.loopRunStart);
    const orphanLoopStart = node('orphan-loop-start', 'missing-loop', FlowNodeTypeEnum.loopRunStart);
    const nodes = [loop, loopStart, orphanLoopStart];
    const isTemplateProtected = (item: StoreNodeItemType) => item.flowNodeType === FlowNodeTypeEnum.loopRunStart;

    expect(isProtectedFromDirectRemoval(nodes, loopStart, isTemplateProtected)).toBe(true);
    expect(isProtectedFromDirectRemoval(nodes, orphanLoopStart, isTemplateProtected)).toBe(false);
  });

  it('does not treat a dragged connection handle as a quick-add click', () => {
    expect(isHandleClick({ x: 100, y: 100 }, { x: 103, y: 102 })).toBe(true);
    expect(isHandleClick({ x: 100, y: 100 }, { x: 118, y: 104 })).toBe(false);
  });

  it('uses Delete, not Backspace, as the workflow delete shortcut', () => {
    expect(isWorkflowDeleteShortcut('Delete')).toBe(true);
    expect(isWorkflowDeleteShortcut('Backspace')).toBe(false);
  });

  it('disables Vue Flow built-in delete shortcut so Backspace cannot bypass workflow shortcuts', () => {
    expect(WORKFLOW_VUE_FLOW_DELETE_KEY_CODE).toBeNull();
  });

  it('ignores workflow shortcuts from modal property editors', () => {
    const modalTarget = {
      closest: (selector: string) => (selector.includes('.ant-modal') ? {} : null),
    };

    expect(shouldIgnoreWorkflowShortcut(modalTarget)).toBe(true);
  });

  it('ignores workflow shortcuts while a visible popup is open even when the key target is body', () => {
    const bodyTarget = {
      closest: () => null,
    };
    const visiblePopupRoot = {
      querySelector: (selector: string) => (selector.includes('.ant-modal-wrap') ? {} : null),
    };

    expect(shouldIgnoreWorkflowShortcut(bodyTarget, visiblePopupRoot)).toBe(true);
  });

  it('does not ignore workflow shortcuts after a hidden node operation dropdown keeps focus', () => {
    const hiddenDropdownMenuItem = {
      closest: (selector: string) =>
        selector.includes('.ant-dropdown') && !selector.includes('.ant-dropdown:not') ? {} : null,
    };

    expect(shouldIgnoreWorkflowShortcut(hiddenDropdownMenuItem)).toBe(false);
  });

  it('allows workflow shortcuts from node controls when a node is selected', () => {
    const nodeInputTarget = {
      closest: (selector: string) => (selector.includes('input') ? {} : null),
    };

    expect(shouldIgnoreWorkflowShortcut(nodeInputTarget, undefined, { hasWorkflowSelection: true })).toBe(false);
  });

  it('allows workflow shortcuts with selected nodes despite stale transient popups', () => {
    const bodyTarget = {
      closest: () => null,
    };
    const staleDropdownRoot = {
      querySelector: (selector: string) => (selector.includes('.ant-dropdown:not') ? {} : null),
    };

    expect(shouldIgnoreWorkflowShortcut(bodyTarget, staleDropdownRoot, { hasWorkflowSelection: true })).toBe(false);
  });

  it('keeps blocking modals protected even when a node is selected', () => {
    const modalTarget = {
      closest: (selector: string) => (selector.includes('.ant-modal') ? {} : null),
    };

    expect(shouldIgnoreWorkflowShortcut(modalTarget, undefined, { hasWorkflowSelection: true })).toBe(true);
  });

  it('pastes from local workflow clipboard when browser clipboard is unavailable', async () => {
    const clipboard = {
      writeText: jest.fn().mockRejectedValue(new Error('denied')),
      readText: jest.fn().mockRejectedValue(new Error('denied')),
    };
    const payload = { nodes: [node('copied')], edges: [] };

    await writeWorkflowClipboardPayload(payload, clipboard);

    expect(await readWorkflowClipboardPayload(clipboard)).toEqual(
      expect.objectContaining({ nodes: [expect.objectContaining({ nodeId: 'copied' })] }),
    );
  });

  it('detects selected input text so copy can stay native', () => {
    expect(hasNativeTextSelection({ selectionStart: 1, selectionEnd: 4 })).toBe(true);
    expect(hasNativeTextSelection({ selectionStart: 2, selectionEnd: 2 })).toBe(false);
  });

  it('detects selected text on the active element when the key target is not the input', () => {
    expect(
      hasNativeTextSelection(
        {},
        {
          activeElement: { selectionStart: 1, selectionEnd: 4 },
        },
      ),
    ).toBe(true);
  });

  it('keeps copy and paste native inside text editors', () => {
    const inputTarget = {
      closest: (selector: string) => (selector.includes('input') ? {} : null),
    };

    expect(shouldUseNativeTextShortcut('c', inputTarget)).toBe(true);
    expect(shouldUseNativeTextShortcut('v', inputTarget)).toBe(true);
  });

  it('uses native copy for selected page text outside editors', () => {
    const bodyTarget = { closest: () => null };
    const pageSelectionRoot = {
      getSelection: () => ({ toString: () => 'selected text' }),
    };

    expect(shouldUseNativeTextShortcut('c', bodyTarget, pageSelectionRoot)).toBe(true);
    expect(shouldUseNativeTextShortcut('v', bodyTarget, pageSelectionRoot)).toBe(false);
  });

  it('treats real DOM node size as measured even before Vue Flow dimensions update', () => {
    const root = {
      querySelector: (selector: string) =>
        selector.includes('node-a') ? { offsetWidth: 240, offsetHeight: 120 } : null,
    };

    const result = getMeasuredNodeElementUpdates(['node-a'], root);

    expect(result.allMeasured).toBe(true);
    expect(result.updates).toEqual([
      expect.objectContaining({ id: 'node-a', forceUpdate: true }),
    ]);
  });
});
