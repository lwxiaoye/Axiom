<template>
  <g class="wf-edge" @mouseenter="hovered = true" @mouseleave="hovered = false">
    <BaseEdge :id="id" :path="path" :style="edgeStyle" :marker-end="markerEnd" />
    <!-- 加宽的透明交互路径，便于悬停命中（蓝本 hoverEdgeId 行为） -->
    <path :d="path" fill="none" stroke="transparent" :stroke-width="16" />
    <EdgeLabelRenderer>
      <button
        v-if="hovered || selected"
        class="wf-edge-delete"
        type="button"
        title="删除连线"
        :style="{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }"
        @click.stop="handleDelete"
        @mouseenter="hovered = true"
      >
        <CloseOutlined />
      </button>
    </EdgeLabelRenderer>
  </g>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { BaseEdge, EdgeLabelRenderer, Position, getSmoothStepPath, useVueFlow } from '@vue-flow/core';
import { CloseOutlined } from '@ant-design/icons-vue';

/** 蓝本 ButtonEdge：smoothstep borderRadius 60、默认 #94B5FF、悬停/选中 #487FFF 加粗 */
const props = defineProps<{
  id: string;
  sourceX: number;
  sourceY: number;
  targetX: number;
  targetY: number;
  sourcePosition: Position;
  targetPosition: Position;
  selected?: boolean;
  markerEnd?: string;
  /** 工具边判定：sourceHandleId === 'selectedTools'（蓝本紫色无箭头工具边） */
  sourceHandleId?: string | null;
  /** data.status：调试轨迹回写的边调度状态（waiting/active/skipped） */
  data?: { status?: string };
}>();

const { removeEdges } = useVueFlow();

const hovered = ref(false);

const pathData = computed(() =>
  getSmoothStepPath({
    sourceX: props.sourceX,
    sourceY: props.sourceY,
    sourcePosition: props.sourcePosition,
    // 蓝本：target 在左侧时终点回缩 7px，避免箭头压住 handle
    targetX: props.targetPosition === Position.Left ? props.targetX - 7 : props.targetX,
    targetY: props.targetY,
    targetPosition: props.targetPosition,
    borderRadius: 60,
  })
);

const path = computed(() => pathData.value[0]);
const labelX = computed(() => pathData.value[1]);
const labelY = computed(() => pathData.value[2]);

const highlighted = computed(() => hovered.value || !!props.selected);

const isToolEdge = computed(() => props.sourceHandleId === 'selectedTools');

/** 调试状态色（蓝本 ChatTest 边状态）：active 主蓝加粗 / waiting 虚线 / skipped 灰淡化；工具边紫色 */
const edgeStyle = computed(() => {
  if (isToolEdge.value) {
    return { stroke: highlighted.value ? '#6f5dd7' : '#b3a7ec', strokeWidth: highlighted.value ? 4 : 3 };
  }
  if (highlighted.value) return { stroke: '#487FFF', strokeWidth: 4 };
  switch (props.data?.status) {
    case 'active':
      return { stroke: '#487FFF', strokeWidth: 4 };
    case 'waiting':
      return { stroke: '#94B5FF', strokeWidth: 3, strokeDasharray: '8 6' };
    case 'skipped':
      return { stroke: '#CBD5E1', strokeWidth: 3, opacity: 0.7 };
    default:
      return { stroke: '#94B5FF', strokeWidth: 3 };
  }
});

function handleDelete() {
  removeEdges([props.id]);
}
</script>

<style lang="less">
.wf-edge-delete {
  position: absolute;
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  padding: 0;
  border: 1px solid #e2e8f0;
  border-radius: 999px;
  background: #fff;
  color: #487fff;
  font-size: 11px;
  cursor: pointer;
  pointer-events: all;
  z-index: 1000;
  box-shadow: 0 2px 8px rgba(15, 23, 42, 0.12);

  &:hover {
    background: #487fff;
    border-color: #487fff;
    color: #fff;
  }
}
</style>
