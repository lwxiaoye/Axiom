export const DEFAULT_RUN_PRESENTATION_PRESET = 'default';
export const CAMPUS_WELCOME_PRESENTATION_PRESET = 'campus-welcome-v1';

export type RunPresentationPresetOption = {
  key: string;
  name: string;
  description: string;
};

/**
 * 搭建器可选择的“衣服”目录。预览图由选择器按 key 绑定，运行组件由 registry.ts 按 key 绑定。
 * 目录与运行注册表分开，纯数据层可以独立测试，也不会把 Vue 组件带进表单编译链路。
 */
export const RUN_PRESENTATION_PRESETS: readonly RunPresentationPresetOption[] = [
  {
    key: DEFAULT_RUN_PRESENTATION_PRESET,
    name: '标准外观',
    description: '平台默认的简洁黑白运行页',
  },
  {
    key: CAMPUS_WELCOME_PRESENTATION_PRESET,
    name: '校园迎新',
    description: '校园蓝图贯穿三栏，迎新插画使用透明抠图',
  },
];

const RUN_PRESENTATION_PRESET_KEYS = new Set(RUN_PRESENTATION_PRESETS.map((item) => item.key));

export function normalizeRunPresentationPreset(value: unknown) {
  const key = typeof value === 'string' ? value.trim() : '';
  return RUN_PRESENTATION_PRESET_KEYS.has(key) ? key : DEFAULT_RUN_PRESENTATION_PRESET;
}
