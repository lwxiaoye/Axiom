<template>
  <div ref="pickerWrapRef" class="model-picker-wrap">
    <button
      ref="modelTriggerRef"
      :class="['model-picker-trigger', { active: isOpen }]"
      type="button"
      :title="selectedLabel"
      aria-haspopup="listbox"
      :aria-expanded="isOpen"
      aria-controls="center-model-picker-list"
      @click.stop="toggleOpen"
    >
      <span class="model-picker-label">{{ selectedLabel }}</span>
      <PremiumChevron class="trigger-chevron" :direction="isOpen ? 'up' : 'down'" :size="14" interactive />
    </button>

    <Teleport to="body" :disabled="!isCompactPicker">
      <button
        v-if="isOpen && isCompactPicker"
        type="button"
        class="model-picker-backdrop"
        aria-label="关闭模型选择"
        @click.stop="closePicker(true)"
      ></button>
      <div
        v-if="isOpen"
        :class="['model-picker-panel', `placement-${pickerPlacement}`]"
        :style="pickerPanelStyle"
        :role="isCompactPicker ? 'dialog' : undefined"
        :aria-modal="isCompactPicker ? 'true' : undefined"
        :aria-label="isCompactPicker ? '选择模型' : undefined"
        @click.stop
        @keydown.esc.stop="closePicker(true)"
      >
        <div class="model-picker-heading">
          <strong>选择模型</strong>
          <div class="model-picker-heading-actions">
            <div class="provider-filter-wrap">
              <button
                class="provider-filter-trigger"
                type="button"
                aria-haspopup="menu"
                :aria-expanded="providerMenuOpen"
                @click="providerMenuOpen = !providerMenuOpen"
              >
                <span>{{ activeProviderLabel }}</span>
                <PremiumChevron
                  :class="['provider-filter-chevron', { active: providerMenuOpen }]"
                  :direction="providerMenuOpen ? 'up' : 'down'"
                  :size="13"
                  interactive
                />
              </button>
              <div v-if="providerMenuOpen" class="provider-filter-menu" role="menu">
                <button
                  v-for="provider in providerOptions"
                  :key="provider.key"
                  :class="['provider-filter-option', { active: provider.key === activeProviderKey }]"
                  type="button"
                  role="menuitemradio"
                  :aria-checked="provider.key === activeProviderKey"
                  @click="selectProvider(provider.key)"
                >
                  <span>{{ provider.optionLabel }}</span>
                  <CheckOutlined v-if="provider.key === activeProviderKey" />
                </button>
              </div>
            </div>
            <button
              ref="modelCloseRef"
              type="button"
              class="model-picker-close"
              aria-label="关闭模型选择"
              @click="closePicker(true)"
            >
              <CloseOutlined />
            </button>
          </div>
        </div>

        <label class="model-picker-search">
          <SearchOutlined />
          <input
            ref="searchInputRef"
            v-model="searchKeyword"
            type="search"
            placeholder="搜索模型或厂商"
            aria-label="搜索模型或厂商"
            autocomplete="off"
            spellcheck="false"
          />
        </label>

        <div
          v-if="groupedModels.length"
          id="center-model-picker-list"
          class="model-picker-list"
          role="listbox"
          aria-label="可用模型"
        >
          <section v-for="group in groupedModels" :key="group.provider.key" class="model-provider-group">
            <div class="model-provider-heading">{{ group.provider.label }}</div>
            <button
              v-for="model in group.models"
              :key="model.id"
              :class="['model-picker-item', { active: model.id === selectedId }]"
              type="button"
              role="option"
              :aria-selected="model.id === selectedId"
              :title="model.name"
              @click="handleSelect(model.id)"
            >
              <strong>{{ model.name }}</strong>
              <span v-if="model.id === selectedId || model.is_default" class="model-state">
                <em v-if="model.is_default">默认</em>
                <CheckOutlined v-if="model.id === selectedId" class="model-selected-icon" aria-hidden="true" />
              </span>
            </button>
          </section>
        </div>

        <div v-else class="model-picker-empty">
          <LoadingOutlined v-if="modelsLoading" spin />
          <SearchOutlined v-else />
          <strong>{{ modelsLoading ? '正在加载模型' : '没有找到匹配模型' }}</strong>
          <span v-if="!modelsLoading">换个关键词或厂商试试</span>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue';
import { CheckOutlined, CloseOutlined, LoadingOutlined, SearchOutlined } from '@ant-design/icons-vue';
import { useMediaQuery } from '@vueuse/core';
import PremiumChevron from './PremiumChevron.vue';
import { getAgentModels, type AgentModelItem } from '../agentApi';
import { usePickerPlacement } from '../composables/usePickerPlacement';

interface ModelProvider {
  key: string;
  label: string;
  optionLabel: string;
  order: number;
}

interface ModelItem extends AgentModelItem {
  provider: ModelProvider;
}

interface ModelGroup {
  provider: ModelProvider;
  models: ModelItem[];
}

const ALL_PROVIDERS: ModelProvider = {
  key: 'all',
  label: '全部厂商',
  optionLabel: '全部厂商',
  order: -1,
};

const FALLBACK_PROVIDER: ModelProvider = {
  key: 'other',
  label: '其他模型',
  optionLabel: '其他模型',
  order: 99,
};

const PROVIDER_RULES: Array<{ test: RegExp; provider: ModelProvider }> = [
  {
    test: /deepseek/i,
    provider: { key: 'deepseek', label: 'DEEPSEEK', optionLabel: 'DeepSeek', order: 0 },
  },
  {
    test: /glm|chatglm/i,
    provider: { key: 'glm', label: '智谱 GLM', optionLabel: '智谱 GLM', order: 1 },
  },
  {
    test: /qwen|qwq/i,
    provider: { key: 'qwen', label: '通义千问', optionLabel: '通义千问', order: 2 },
  },
  {
    test: /kimi|moonshot/i,
    provider: { key: 'kimi', label: 'KIMI', optionLabel: 'Kimi', order: 3 },
  },
  {
    test: /doubao/i,
    provider: { key: 'doubao', label: '豆包', optionLabel: '豆包', order: 4 },
  },
  {
    test: /claude/i,
    provider: { key: 'anthropic', label: 'ANTHROPIC', optionLabel: 'Anthropic', order: 5 },
  },
  {
    test: /gemini/i,
    provider: { key: 'gemini', label: 'GOOGLE GEMINI', optionLabel: 'Google Gemini', order: 6 },
  },
  {
    test: /gpt|(^|[-_])o[134]([-. _]|$)/i,
    provider: { key: 'openai', label: 'OPENAI', optionLabel: 'OpenAI', order: 7 },
  },
  {
    test: /mistral|mixtral/i,
    provider: { key: 'mistral', label: 'MISTRAL', optionLabel: 'Mistral', order: 8 },
  },
  {
    test: /llama/i,
    provider: { key: 'llama', label: 'LLAMA', optionLabel: 'Llama', order: 9 },
  },
];

const props = defineProps<{
  modelValue?: string;
}>();

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void;
}>();

const isOpen = ref(false);
const pickerWrapRef = ref<HTMLElement | null>(null);
const modelTriggerRef = ref<HTMLButtonElement | null>(null);
const modelCloseRef = ref<HTMLButtonElement | null>(null);
const isCompactPicker = useMediaQuery('(max-width: 1024px)');
const providerMenuOpen = ref(false);
const activeProviderKey = ref(ALL_PROVIDERS.key);
const searchKeyword = ref('');
const models = ref<ModelItem[]>([]);
const modelsLoading = ref(true);
const searchInputRef = ref<HTMLInputElement | null>(null);
const PICKER_OPEN_EVENT = 'center-chat-picker-open';
const STORAGE_KEY = 'agent-active-model';
const { placement: pickerPlacement, panelStyle: pickerPanelStyle } = usePickerPlacement(pickerWrapRef, isOpen, {
  preferredHeight: 480,
});

const selectedId = computed(() => props.modelValue || '');

const selectedLabel = computed(() => {
  const model = models.value.find((item) => item.id === selectedId.value);
  return model?.name || selectedId.value || '选择模型';
});

const availableProviders = computed(() => {
  const providerMap = new Map<string, ModelProvider>();
  models.value.forEach((model) => providerMap.set(model.provider.key, model.provider));
  return [...providerMap.values()].sort((left, right) => left.order - right.order);
});

const providerOptions = computed(() => [ALL_PROVIDERS, ...availableProviders.value]);

const activeProviderLabel = computed(() => {
  if (activeProviderKey.value === ALL_PROVIDERS.key) return '按厂商浏览';
  return availableProviders.value.find((provider) => provider.key === activeProviderKey.value)?.optionLabel || '按厂商浏览';
});

const filteredModels = computed(() => {
  const keyword = searchKeyword.value.trim().toLowerCase();
  return models.value.filter((model) => {
    const providerMatches = activeProviderKey.value === ALL_PROVIDERS.key || model.provider.key === activeProviderKey.value;
    const keywordMatches =
      !keyword ||
      model.id.toLowerCase().includes(keyword) ||
      model.name.toLowerCase().includes(keyword) ||
      model.provider.label.toLowerCase().includes(keyword) ||
      model.provider.optionLabel.toLowerCase().includes(keyword);
    return providerMatches && keywordMatches;
  });
});

const groupedModels = computed<ModelGroup[]>(() => {
  const groupMap = new Map<string, ModelGroup>();
  filteredModels.value.forEach((model) => {
    const existingGroup = groupMap.get(model.provider.key);
    if (existingGroup) {
      existingGroup.models.push(model);
      return;
    }
    groupMap.set(model.provider.key, { provider: model.provider, models: [model] });
  });
  return [...groupMap.values()].sort((left, right) => left.provider.order - right.provider.order);
});

function resolveModelProvider(model: AgentModelItem): ModelProvider {
  const identity = `${model.id} ${model.name}`;
  return PROVIDER_RULES.find((rule) => rule.test.test(identity))?.provider || FALLBACK_PROVIDER;
}

function toggleOpen() {
  if (isOpen.value) {
    closePicker();
    return;
  }
  document.dispatchEvent(new CustomEvent(PICKER_OPEN_EVENT, { detail: 'model' }));
  isOpen.value = true;
  providerMenuOpen.value = false;
  activeProviderKey.value = ALL_PROVIDERS.key;
  searchKeyword.value = '';
  nextTick(() => {
    if (isCompactPicker.value) modelCloseRef.value?.focus();
    else searchInputRef.value?.focus();
  });
}

function closePicker(restoreFocus = false) {
  isOpen.value = false;
  providerMenuOpen.value = false;
  if (restoreFocus) nextTick(() => modelTriggerRef.value?.focus());
}

function selectProvider(providerKey: string) {
  activeProviderKey.value = providerKey;
  providerMenuOpen.value = false;
  if (!isCompactPicker.value) nextTick(() => searchInputRef.value?.focus());
}

function handleClickOutside() {
  closePicker();
}

function handlePickerOpen(event: Event) {
  if ((event as CustomEvent<string>).detail !== 'model') closePicker();
}

async function loadModels() {
  modelsLoading.value = true;
  try {
    const list = await getAgentModels();
    models.value = list.map((item) => ({ ...item, provider: resolveModelProvider(item) }));
    const savedId = localStorage.getItem(STORAGE_KEY);
    // v2.73: 默认优先 deepseek-v4-flash（本地 free-quota 下 qwen 默认常 403 导致「任务执行失败」）
    // 有用户显式缓存时仍尊重缓存。
    const preferredIds = ['deepseek-v4-flash', 'deepseek-chat'];
    const preferred = preferredIds
      .map((id) => models.value.find((model) => model.id === id))
      .find(Boolean);
    const defaultModel = models.value.find((model) => model.is_default);
    const fallbackId = preferred?.id || defaultModel?.id || models.value[0]?.id || '';
    const selectedIsValid = Boolean(
      selectedId.value && models.value.some((model) => model.id === selectedId.value),
    );
    // 已打开 Thread 时父层传入的是持久会话设置，不能再用本机“新对话默认值”覆盖。
    if (selectedIsValid) {
      return;
    }
    if (savedId && models.value.some((model) => model.id === savedId)) {
      if (savedId !== selectedId.value) emit('update:modelValue', savedId);
    } else if (!selectedId.value || !models.value.some((model) => model.id === selectedId.value)) {
      if (fallbackId) {
        emit('update:modelValue', fallbackId);
        // 无缓存时把稳定默认写回，避免下次又落到 free-quota 默认
        if (!savedId && preferred?.id === fallbackId) {
          localStorage.setItem(STORAGE_KEY, fallbackId);
        }
      }
    }
  } catch (error) {
    console.error('Failed to load models:', error);
  } finally {
    modelsLoading.value = false;
  }
}

function handleSelect(modelId: string) {
  emit('update:modelValue', modelId);
  closePicker();
}

onMounted(() => {
  loadModels();
  document.addEventListener('click', handleClickOutside);
  document.addEventListener(PICKER_OPEN_EVENT, handlePickerOpen);
});

onUnmounted(() => {
  document.removeEventListener('click', handleClickOutside);
  document.removeEventListener(PICKER_OPEN_EVENT, handlePickerOpen);
});
</script>

<style scoped>
.model-picker-wrap {
  position: relative;
}

.model-picker-backdrop,
.model-picker-close {
  display: none;
}

.model-picker-trigger {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 34px;
  max-width: 230px;
  padding: 0 10px;
  border: 1px solid #e0e3e8;
  border-radius: 9px;
  background: #fff;
  color: #343a46;
  cursor: pointer;
  font-family: inherit;
  font-size: 13px;
  font-weight: 520;
  transition:
    border-color 0.18s ease,
    background-color 0.18s ease,
    box-shadow 0.18s ease;
}

.model-picker-trigger:hover,
.model-picker-trigger.active {
  border-color: #c8cdd6;
  background: #fafbfc;
}

.model-picker-trigger.active {
  background: #f7f8fa;
}

.model-picker-trigger:focus-visible,
.provider-filter-trigger:focus-visible,
.provider-filter-option:focus-visible,
.model-picker-item:focus-visible {
  outline: 1px solid #8f96a3;
  outline-offset: 0;
}

.model-picker-label {
  overflow: hidden;
  min-width: 0;
  max-width: 190px;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.trigger-chevron,
.provider-filter-chevron {
  flex: none;
  color: #606875;
}

.model-picker-panel {
  position: absolute;
  right: 0;
  display: flex;
  /* 300px 而不是原来的 400（2026-07-28 用户拍板：显得太宽）。这个宽度是量出来的不是拍的：
     面板里最宽的一行是标题行＝「选择模型」71px + 「按厂商浏览」116px ≈ 200px，模型名那列
     实测最宽 63px；300 - 32(padding) = 268px 的内容区对两者都还有富余，长模型 id
     （claude-sonnet-4-5-2025xxxx 这类）也放得下。 */
  width: min(300px, calc(100vw - 24px));
  max-height: var(--picker-available-height, 480px);
  flex-direction: column;
  padding: 16px;
  border: 1px solid #e0e3e8;
  border-radius: 17px;
  background: #fff;
  box-shadow:
    0 24px 64px rgba(15, 23, 42, 0.14),
    0 6px 20px rgba(15, 23, 42, 0.06);
  transform-origin: right bottom;
  z-index: 100;
  animation: picker-enter 0.2s cubic-bezier(0.22, 1, 0.36, 1) both;
}

.model-picker-panel.placement-top {
  bottom: calc(100% + 10px);
  transform-origin: right bottom;
}

.model-picker-panel.placement-bottom {
  top: calc(100% + 10px);
  transform-origin: right top;
  animation-name: picker-enter-down;
}

.model-picker-heading {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
}

.model-picker-heading-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.model-picker-heading > strong {
  color: #111827;
  font-size: 18px;
  font-weight: 700;
  letter-spacing: -0.02em;
}

.provider-filter-wrap {
  position: relative;
}

.provider-filter-trigger {
  display: inline-flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  min-width: 116px;
  height: 34px;
  padding: 0 11px;
  border: 1px solid #d9dde4;
  border-radius: 9px;
  background: #fff;
  color: #353b46;
  cursor: pointer;
  font-family: inherit;
  font-size: 12.5px;
  transition:
    border-color 0.16s ease,
    background-color 0.16s ease;
}

.provider-filter-trigger:hover {
  border-color: #bfc5cf;
  background: #fafbfc;
}

.provider-filter-menu {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  width: 154px;
  max-height: 250px;
  overflow-y: auto;
  padding: 5px;
  border: 1px solid #e1e4e9;
  border-radius: 10px;
  background: #fff;
  box-shadow: 0 14px 36px rgba(15, 23, 42, 0.14);
  z-index: 2;
  animation: filter-enter 0.16s cubic-bezier(0.22, 1, 0.36, 1) both;
}

.provider-filter-option {
  display: flex;
  width: 100%;
  min-height: 34px;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 7px 9px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: #4a5260;
  cursor: pointer;
  font-family: inherit;
  font-size: 12px;
  text-align: left;
}

.provider-filter-option:hover,
.provider-filter-option.active {
  background: #f2f4ff;
  color: #3654ca;
}

.provider-filter-option :deep(.anticon) {
  color: #315cec;
  font-size: 10px;
}

.model-picker-search {
  display: flex;
  flex: none;
  height: 38px;
  align-items: center;
  gap: 10px;
  padding: 0 12px;
  border: 1px solid #e8eaee;
  border-radius: 9px;
  background: #f6f7f9;
  color: #7c8493;
  transition:
    border-color 0.16s ease,
    background-color 0.16s ease;
}

.model-picker-search:focus-within {
  border-color: #cbd0d8;
  background: #f6f7f9;
}

.model-picker-search input {
  width: 100%;
  min-width: 0;
  padding: 0;
  border: 0;
  outline: 0;
  appearance: none;
  background: transparent;
  color: #202632;
  font-family: inherit;
  font-size: 13px;
}

.model-picker-search input::placeholder {
  color: #969daa;
}

.model-picker-search input::-webkit-search-cancel-button {
  display: none;
}

.model-picker-list {
  min-height: 0;
  flex: 1;
  margin: 8px -5px -6px 0;
  padding: 0 5px 6px 0;
  overflow-y: auto;
  scrollbar-color: transparent transparent;
  scrollbar-width: thin;
}

.model-picker-list:hover {
  scrollbar-color: #cbd1dc transparent;
}

.model-picker-list::-webkit-scrollbar {
  width: 5px;
}

.model-picker-list::-webkit-scrollbar-thumb {
  border-radius: 999px;
  background: transparent;
}

.model-picker-list:hover::-webkit-scrollbar-thumb {
  background: #cbd1dc;
}

.model-provider-group {
  padding: 12px 0 10px;
  border-bottom: 1px solid #e9ebef;
}

.model-provider-group:last-child {
  padding-bottom: 0;
  border-bottom: 0;
}

.model-provider-heading {
  padding: 0 3px 7px;
  color: #777f8e;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.045em;
  line-height: 1.3;
}

.model-picker-item {
  display: flex;
  width: 100%;
  min-height: 43px;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 8px 11px;
  border: 0;
  border-radius: 9px;
  background: transparent;
  color: #202632;
  cursor: pointer;
  font-family: inherit;
  text-align: left;
  transition:
    background-color 0.16s ease,
    color 0.16s ease,
    transform 0.18s cubic-bezier(0.22, 1, 0.36, 1);
}

.model-picker-item:hover {
  background: #f6f7f9;
  transform: translateX(1px);
}

.model-picker-item.active {
  background: #f1f3ff;
  color: #17265c;
}

.model-picker-item > strong {
  overflow: hidden;
  min-width: 0;
  font-size: 13px;
  font-weight: 590;
  line-height: 1.35;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.model-state {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 10px;
}

.model-state em {
  color: #315cec;
  font-size: 11px;
  font-style: normal;
}

.model-selected-icon {
  color: #315cec;
  font-size: 13px;
}

.model-picker-empty {
  display: flex;
  min-height: 180px;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 7px;
  color: #adb3bd;
}

.model-picker-empty > :first-child {
  margin-bottom: 2px;
  font-size: 22px;
}

.model-picker-empty strong {
  color: #656d7a;
  font-size: 12.5px;
  font-weight: 600;
}

.model-picker-empty span {
  font-size: 11.5px;
}

@keyframes picker-enter {
  from {
    opacity: 0;
    transform: translateY(7px) scale(0.97);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

@keyframes picker-enter-down {
  from {
    opacity: 0;
    transform: translateY(-7px) scale(0.97);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

@keyframes filter-enter {
  from {
    opacity: 0;
    transform: translateY(-4px) scale(0.98);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

/* 手机/iPad 上模型清单与 + 菜单共用底部面板形态，不让键盘和输入框夹住绝对定位浮层。 */
@media (max-width: 1024px) {
  .model-picker-backdrop {
    position: fixed;
    z-index: 4190;
    inset: 0;
    display: block;
    padding: 0;
    border: 0;
    background: rgba(17, 24, 39, 0.3);
    cursor: default;
    touch-action: none;
    backdrop-filter: blur(2px);
    animation: model-backdrop-enter 0.18s ease both;
  }

  .model-picker-panel,
  .model-picker-panel.placement-top,
  .model-picker-panel.placement-bottom {
    position: fixed;
    z-index: 4200;
    top: auto;
    right: auto;
    bottom: calc(12px + env(safe-area-inset-bottom));
    left: 50%;
    width: min(520px, calc(100vw - 24px));
    height: min(72dvh, 620px);
    max-height: calc(100dvh - env(safe-area-inset-top) - env(safe-area-inset-bottom) - 24px);
    box-sizing: border-box;
    padding: 14px;
    border-radius: 22px;
    box-shadow: 0 24px 72px rgba(15, 23, 42, 0.22), 0 4px 18px rgba(15, 23, 42, 0.08);
    transform: translateX(-50%);
    transform-origin: center bottom;
    overscroll-behavior: contain;
    animation: model-sheet-enter 0.22s cubic-bezier(0.22, 1, 0.36, 1) both;
  }

  .model-picker-heading {
    min-height: 44px;
    margin-bottom: 10px;
  }

  .model-picker-heading-actions {
    gap: 6px;
  }

  .model-picker-close {
    display: grid;
    width: 42px;
    height: 42px;
    flex: none;
    padding: 0;
    place-items: center;
    border: 0;
    border-radius: 13px;
    background: #f2f3f5;
    color: #4d525c;
    cursor: pointer;
    font-size: 15px;
  }

  .model-picker-close:hover {
    background: #e8e9ec;
    color: #111;
  }

  .model-picker-close:focus-visible {
    outline: 2px solid #7786d9;
    outline-offset: 1px;
  }

  .model-picker-list {
    overscroll-behavior: contain;
    touch-action: pan-y;
  }

  .model-picker-item {
    min-height: 48px;
    padding: 9px 11px;
  }
}

@keyframes model-sheet-enter {
  from {
    opacity: 0;
    transform: translate(-50%, 18px) scale(0.985);
  }
  to {
    opacity: 1;
    transform: translate(-50%, 0) scale(1);
  }
}

@keyframes model-backdrop-enter {
  from { opacity: 0; }
  to { opacity: 1; }
}

@media (max-width: 560px) {
  .model-picker-wrap {
    min-width: 0;
    max-width: min(34vw, 150px);
  }

  .model-picker-trigger {
    width: 100%;
    min-width: 0;
    max-width: 100%;
    gap: 5px;
    padding: 0 8px;
  }

  .model-picker-label {
    max-width: none;
  }

  .model-picker-heading > strong {
    font-size: 16px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .model-picker-panel,
  .model-picker-backdrop,
  .provider-filter-menu {
    animation: none;
  }

  .model-picker-trigger,
  .trigger-chevron,
  .provider-filter-chevron,
  .provider-filter-trigger,
  .model-picker-search,
  .model-picker-item {
    transition: none;
  }
}
</style>
