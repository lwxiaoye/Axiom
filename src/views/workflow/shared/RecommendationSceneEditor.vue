<template>
  <div class="recommendation-scene-editor">
    <div class="recommendation-editor-head">
      <span>场景分组</span>
      <button type="button" class="recommendation-add-btn" @click="addScene">
        <PlusOutlined />
        添加场景
      </button>
    </div>

    <div v-if="modelValue.length" class="recommendation-scene-list">
      <section v-for="(scene, sceneIndex) in modelValue" :key="scene.key" class="recommendation-scene-card">
        <header class="recommendation-scene-head">
          <a-input
            size="small"
            :value="scene.label"
            placeholder="场景名称，例如：翻译"
            aria-label="场景名称"
            @change="updateSceneLabel(sceneIndex, ($event.target as HTMLInputElement).value)"
            @blur="normalizeSceneLabel(sceneIndex)"
          />
          <button type="button" class="recommendation-remove-btn" title="删除场景" @click="removeScene(sceneIndex)">
            <DeleteOutlined />
          </button>
        </header>

        <div v-if="scene.textList.length" class="recommendation-item-list">
          <div v-for="(_item, itemIndex) in scene.textList" :key="itemIndex" class="recommendation-item-row">
            <a-input
              size="small"
              :value="scene.textList[itemIndex]"
              placeholder="例如：把这份文档整理成一页摘要"
              aria-label="推荐内容"
              @change="updateItem(sceneIndex, itemIndex, ($event.target as HTMLInputElement).value)"
              @blur="normalizeItems(sceneIndex)"
            />
            <button
              type="button"
              class="recommendation-remove-btn"
              title="删除推荐内容"
              @click="removeItem(sceneIndex, itemIndex)"
            >
              <DeleteOutlined />
            </button>
          </div>
        </div>

        <button type="button" class="recommendation-add-item" @click="addItem(sceneIndex)">
          <PlusOutlined />
          添加推荐内容
        </button>
      </section>
    </div>

    <button v-else type="button" class="recommendation-empty" @click="addScene">
      <MessageOutlined />
      暂无场景，点击添加
    </button>
  </div>
</template>

<script setup lang="ts">
import { DeleteOutlined, MessageOutlined, PlusOutlined } from '@ant-design/icons-vue';
import type { RecommendationSceneConfig } from '../core/type';
import { getNanoid } from '../core/utils';

const props = defineProps<{
  modelValue: RecommendationSceneConfig[];
}>();

const emit = defineEmits<{
  (event: 'update:modelValue', value: RecommendationSceneConfig[]): void;
}>();

function cloneScenes() {
  return props.modelValue.map((scene) => ({ ...scene, textList: [...scene.textList] }));
}

function commit(scenes: RecommendationSceneConfig[]) {
  emit('update:modelValue', scenes);
}

function addScene() {
  commit([
    ...cloneScenes(),
    { key: getNanoid(), label: `场景 ${props.modelValue.length + 1}`, textList: [] },
  ]);
}

function removeScene(sceneIndex: number) {
  commit(cloneScenes().filter((_, index) => index !== sceneIndex));
}

function updateSceneLabel(sceneIndex: number, label: string) {
  const scenes = cloneScenes();
  if (!scenes[sceneIndex]) return;
  scenes[sceneIndex].label = label;
  commit(scenes);
}

function normalizeSceneLabel(sceneIndex: number) {
  const scenes = cloneScenes();
  if (!scenes[sceneIndex]) return;
  scenes[sceneIndex].label = scenes[sceneIndex].label.trim() || `场景 ${sceneIndex + 1}`;
  commit(scenes);
}

function addItem(sceneIndex: number) {
  const scenes = cloneScenes();
  if (!scenes[sceneIndex]) return;
  scenes[sceneIndex].textList.push('');
  commit(scenes);
}

function updateItem(sceneIndex: number, itemIndex: number, value: string) {
  const scenes = cloneScenes();
  if (!scenes[sceneIndex]?.textList[itemIndex] && scenes[sceneIndex]?.textList[itemIndex] !== '') return;
  scenes[sceneIndex].textList[itemIndex] = value;
  commit(scenes);
}

function removeItem(sceneIndex: number, itemIndex: number) {
  const scenes = cloneScenes();
  if (!scenes[sceneIndex]) return;
  scenes[sceneIndex].textList = scenes[sceneIndex].textList.filter((_, index) => index !== itemIndex);
  commit(scenes);
}

function normalizeItems(sceneIndex: number) {
  const scenes = cloneScenes();
  if (!scenes[sceneIndex]) return;
  scenes[sceneIndex].textList = Array.from(
    new Set(scenes[sceneIndex].textList.map((item) => item.trim()).filter(Boolean)),
  );
  commit(scenes);
}
</script>

<style scoped lang="less">
.recommendation-scene-editor { display: grid; gap: 10px; }
.recommendation-editor-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.recommendation-editor-head > span { color: #667085; font-size: 12px; font-weight: 600; }
.recommendation-add-btn,
.recommendation-add-item,
.recommendation-remove-btn,
.recommendation-empty {
  border: 0;
  background: transparent;
  cursor: pointer;
}
.recommendation-add-btn,
.recommendation-add-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: #4f46e5;
  font-size: 12px;
}
.recommendation-scene-list { display: grid; gap: 10px; }
.recommendation-scene-card {
  display: grid;
  gap: 9px;
  padding: 10px;
  border: 1px solid #e4e7ec;
  border-radius: 10px;
  background: #fafbfc;
}
.recommendation-scene-head,
.recommendation-item-row { display: grid; grid-template-columns: minmax(0, 1fr) 28px; align-items: center; gap: 7px; }
.recommendation-scene-head :deep(.ant-input) { color: #344054; font-weight: 600; }
.recommendation-item-list { display: grid; gap: 7px; }
.recommendation-scene-card :deep(.ant-input) { border-color: #dfe3e8; border-radius: 8px; box-shadow: none; }
.recommendation-remove-btn {
  display: inline-flex;
  width: 28px;
  height: 28px;
  align-items: center;
  justify-content: center;
  border-radius: 7px;
  color: #98a2b3;
}
.recommendation-remove-btn:hover { background: #f0f1f3; color: #d92d20; }
.recommendation-add-item { justify-self: start; padding: 3px 2px; }
.recommendation-empty {
  width: 100%;
  min-height: 42px;
  border: 1px dashed #dfe3e8;
  border-radius: 9px;
  color: #98a2b3;
  font-size: 12px;
}
.recommendation-empty:hover { border-color: #c7d2fe; background: #f8fafc; color: #4f46e5; }
</style>
