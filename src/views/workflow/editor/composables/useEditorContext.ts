/**
 * 编辑器上下文：画布模型与选项数据经 provide/inject 下发给节点卡片与渲染器，
 * 避免跨多层组件透传。所有渲染器直接改 graph 内的响应式对象。
 */
import { inject, provide, type Ref } from 'vue';
import type { WorkflowGraphType } from '../../core/type';
import type { WorkflowModelOption } from '../../api/workflow.api';

export type KnowledgeOption = {
  id: string;
  name: string;
  description?: string;
  [key: string]: any;
};

export type WorkflowEditorContext = {
  graph: Ref<WorkflowGraphType>;
  appId: Ref<string>;
  modelOptions: Ref<WorkflowModelOption[]>;
  modelLoading: Ref<boolean>;
  knowledgeOptions: Ref<KnowledgeOption[]>;
  knowledgeLoading: Ref<boolean>;
  /** VIEWER 只读态：节点卡片隐藏编辑菜单、禁用内部编辑器 */
  readonly: Ref<boolean>;
  canRemoveNode: (nodeId: string) => boolean;
  removeNode: (nodeId: string) => void;
  duplicateNode: (nodeId: string) => void;
  quickAddFromHandle: (nodeId: string, sourceHandle: string) => void;
  renameNode: (nodeId: string, name: string) => void;
  /** 切换节点报错捕获（蓝本 catchError）：关闭时同步清理该节点的错误边 */
  setCatchError: (nodeId: string, value: boolean) => void;
};

const KEY = Symbol('workflow-editor-context');

export function provideEditorContext(context: WorkflowEditorContext) {
  provide(KEY, context);
}

export function useEditorContext(): WorkflowEditorContext {
  const context = inject<WorkflowEditorContext>(KEY);
  if (!context) {
    throw new Error('workflow editor context is not provided');
  }
  return context;
}
