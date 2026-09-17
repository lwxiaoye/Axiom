<template>
  <div>
    <div class="run-presentation-picker" role="radiogroup" aria-label="运行页外观试衣间">
      <button
        v-for="option in options"
        :key="option.key"
        type="button"
        role="radio"
        :disabled="disabled || !option.available"
        :aria-checked="normalizedValue === option.key"
        :aria-label="`${option.name}${option.available ? '' : `，${option.availabilityLabel}`}`"
        :data-presentation-key="option.key"
        :class="[
          'presentation-choice',
          { selected: normalizedValue === option.key, unavailable: !option.available },
        ]"
        @pointerdown.stop
        @click.stop="selectPreset(option)"
      >
        <span class="presentation-preview" aria-hidden="true">
          <img v-if="previewFor(option)" :src="previewFor(option)" alt="" />
          <span v-else class="default-preview">
            <i class="default-copy"></i>
            <i class="default-composer"></i>
          </span>
          <span v-if="!option.available" class="availability-badge">{{ option.availabilityLabel }}</span>
        </span>
        <span class="presentation-copy">
          <strong>{{ option.name }}</strong>
          <small>{{ option.description }}</small>
        </span>
        <span class="choice-indicator" aria-hidden="true"><i></i></span>
      </button>
    </div>
    <p v-if="loading" class="catalog-state">正在读取全局皮肤目录…</p>
    <p v-else-if="loadError" class="catalog-state is-error">
      {{ loadError }}；暂时只能新选标准外观。
    </p>
    <p v-else class="catalog-state">这里只展示当前已安装、未删除的皮肤包。</p>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import {
  queryAvailablePresentationPresets,
  type PresentationPresetRecord,
} from '../../../workflow/api/presentation.api';
import { getRunPresentationPreview } from './preview';
import {
  hydratePortableRunSkin,
  releaseHydratedPortableRunSkin,
  type HydratedPortableRunSkin,
} from './portable';
import {
  DEFAULT_RUN_PRESENTATION_PRESET,
  RUN_PRESENTATION_PRESETS,
  normalizeRunPresentationPreset,
  type RunPresentationPresetOption,
} from './catalog';

const props = defineProps<{
  modelValue?: string;
  appId?: string;
  disabled?: boolean;
}>();

const emit = defineEmits<{
  'update:modelValue': [value: string];
}>();

type PickerOption = RunPresentationPresetOption & {
  available: boolean;
  availabilityLabel?: '目录读取失败' | '已卸载';
  portableSkin?: PresentationPresetRecord['portableSkin'];
};

const normalizedValue = computed(() => {
  const key = String(props.modelValue || '').trim();
  if (serverRecords.value.some((record) => record.key === key && record.portableSkin)) return key;
  return normalizeRunPresentationPreset(key) === key ? key : DEFAULT_RUN_PRESENTATION_PRESET;
});
const serverRecords = ref<PresentationPresetRecord[]>([]);
const hydratedPortableSkins = ref(new Map<string, HydratedPortableRunSkin>());
const loading = ref(false);
const loadError = ref('');
let catalogRequest = 0;

const defaultPreset = RUN_PRESENTATION_PRESETS.find(
  (option) => option.key === DEFAULT_RUN_PRESENTATION_PRESET,
)!;

const options = computed<PickerOption[]>(() => {
  const visible: PickerOption[] = [{
    ...defaultPreset,
    available: true,
  }];

  for (const record of serverRecords.value) {
    if (!record.portableSkin || visible.some((option) => option.key === record.key)) continue;
    visible.push({
      key: record.key,
      name: record.name,
      description: record.description,
      available: true,
      portableSkin: record.portableSkin,
    });
  }

  return visible;
});

async function loadCatalog() {
  const request = ++catalogRequest;
  loading.value = true;
  loadError.value = '';
  try {
    const response = await queryAvailablePresentationPresets(props.appId || undefined);
    if (request !== catalogRequest) return;
    const records = Array.isArray(response?.records) ? response.records : [];
    for (const skin of hydratedPortableSkins.value.values()) releaseHydratedPortableRunSkin(skin);
    hydratedPortableSkins.value = new Map();
    serverRecords.value = records.filter((record) => record.key === DEFAULT_RUN_PRESENTATION_PRESET || record.portableSkin);
    const hydrated = new Map<string, HydratedPortableRunSkin>();
    await Promise.all(records.map(async (record) => {
      if (!record.portableSkin) return;
      try {
        hydrated.set(record.key, await hydratePortableRunSkin(record.portableSkin));
      } catch {
        // Catalog remains usable; a broken preview does not change the server-verified catalog.
      }
    }));
    if (request !== catalogRequest) {
      for (const skin of hydrated.values()) releaseHydratedPortableRunSkin(skin);
      return;
    }
    hydratedPortableSkins.value = hydrated;
  } catch (error) {
    if (request !== catalogRequest) return;
    for (const skin of hydratedPortableSkins.value.values()) releaseHydratedPortableRunSkin(skin);
    hydratedPortableSkins.value = new Map();
    serverRecords.value = [];
    loadError.value = '全局皮肤目录暂时无法读取';
  } finally {
    if (request === catalogRequest) loading.value = false;
  }
}

watch(() => props.appId, loadCatalog, { immediate: true });

function previewFor(option: PickerOption) {
  const hydrated = hydratedPortableSkins.value.get(option.key);
  if (hydrated) {
    const previewKey = hydrated.manifest?.assets.find((asset) => asset.key === 'preview')?.key;
    if (previewKey && hydrated.assetUrls[previewKey]) return hydrated.assetUrls[previewKey];
  }
  return getRunPresentationPreview(option.key);
}

function selectPreset(option: PickerOption) {
  if (props.disabled || !option.available) return;
  emit('update:modelValue', option.key);
}

onBeforeUnmount(() => {
  catalogRequest += 1;
  for (const skin of hydratedPortableSkins.value.values()) releaseHydratedPortableRunSkin(skin);
  hydratedPortableSkins.value = new Map();
});
</script>

<style scoped lang="less">
.run-presentation-picker {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.presentation-choice {
  position: relative;
  display: grid;
  min-width: 0;
  gap: 9px;
  padding: 8px;
  border: 1px solid #e1e7ef;
  border-radius: 10px;
  outline: none;
  background: #fff;
  color: #1f2937;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.16s ease, box-shadow 0.16s ease, transform 0.16s ease;
}

.presentation-choice:disabled {
  cursor: not-allowed;
}

.presentation-choice.unavailable {
  border-style: dashed;
  opacity: 0.68;
}

.presentation-choice:hover {
  border-color: #a9bdd2;
  transform: translateY(-1px);
}

.presentation-choice:focus-visible {
  border-color: #2878d2;
  box-shadow: 0 0 0 3px rgba(40, 120, 210, 0.14);
}

.presentation-choice.selected {
  border-color: #2878d2;
  box-shadow: 0 0 0 1px rgba(40, 120, 210, 0.14);
}

.presentation-preview {
  position: relative;
  display: block;
  aspect-ratio: 3.7 / 1;
  overflow: hidden;
  border: 1px solid #edf0f4;
  border-radius: 6px;
  background: #f8fafc;
}

.presentation-preview img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.availability-badge {
  position: absolute;
  top: 6px;
  right: 6px;
  padding: 2px 6px;
  border-radius: 999px;
  background: rgba(30, 41, 59, 0.78);
  color: #fff;
  font-size: 10px;
  line-height: 1.4;
}

.default-preview {
  position: absolute;
  inset: 0;
  background: #fbfbfc;
}

.default-copy,
.default-composer {
  position: absolute;
  left: 50%;
  display: block;
  transform: translateX(-50%);
  border-radius: 999px;
  background: #d9dde3;
}

.default-copy {
  top: 24%;
  width: 28%;
  height: 7%;
  box-shadow: 0 7px 0 #e8eaee;
}

.default-composer {
  bottom: 17%;
  width: 66%;
  height: 24%;
  border: 1px solid #e2e5e9;
  background: #fff;
  box-shadow: 0 3px 8px rgba(15, 23, 42, 0.06);
}

.presentation-copy {
  display: grid;
  min-width: 0;
  gap: 2px;
  padding-right: 18px;
}

.presentation-copy strong {
  font-size: 13px;
  font-weight: 650;
}

.presentation-copy small {
  color: #7a8797;
  font-size: 11px;
  line-height: 1.35;
}

.choice-indicator {
  position: absolute;
  right: 9px;
  bottom: 24px;
  display: grid;
  width: 14px;
  height: 14px;
  place-items: center;
  border: 1px solid #c8d0da;
  border-radius: 50%;
  background: #fff;
}

.presentation-choice.selected .choice-indicator {
  border-color: #2878d2;
  background: #2878d2;
}

.choice-indicator i {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: transparent;
}

.catalog-state {
  margin: 8px 0 0;
  color: #7a8797;
  font-size: 11px;
  line-height: 1.45;
}

.catalog-state.is-error {
  color: #b45309;
}

.presentation-choice.selected .choice-indicator i {
  background: #fff;
}

@media (max-width: 560px) {
  .run-presentation-picker {
    grid-template-columns: 1fr;
  }
}

@media (prefers-reduced-motion: reduce) {
  .presentation-choice {
    transition: none;
  }
}
</style>
