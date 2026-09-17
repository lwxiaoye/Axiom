/**
 * GPUStack 相关枚举与展示映射（值与后端 GpuStackEnums 一致，来自真实 OpenAPI v2.2.0）。
 */

/** 模型实例状态 -> 徽标颜色与文案 */
export const MODEL_INSTANCE_STATE_MAP: Record<string, { text: string; color: string }> = {
  initializing: { text: '初始化', color: 'blue' },
  pending: { text: '等待中', color: 'orange' },
  starting: { text: '启动中', color: 'processing' },
  running: { text: '运行中', color: 'green' },
  scheduled: { text: '已调度', color: 'cyan' },
  error: { text: '错误', color: 'red' },
  downloading: { text: '下载中', color: 'gold' },
  analyzing: { text: '分析中', color: 'purple' },
  unreachable: { text: '不可达', color: 'red' },
};

/** Worker 节点状态 -> 徽标颜色与文案 */
export const WORKER_STATE_MAP: Record<string, { text: string; color: string }> = {
  not_ready: { text: '未就绪', color: 'orange' },
  ready: { text: '就绪', color: 'green' },
  unreachable: { text: '不可达', color: 'red' },
  pending: { text: '等待中', color: 'blue' },
  provisioning: { text: '供应中', color: 'processing' },
  initializing: { text: '初始化', color: 'blue' },
  deleting: { text: '删除中', color: 'volcano' },
  error: { text: '错误', color: 'red' },
  maintenance: { text: '维护中', color: 'gold' },
};

/** 模型来源 -> 文案 */
export const SOURCE_MAP: Record<string, string> = {
  huggingface: 'HuggingFace',
  model_scope: 'ModelScope',
  local_path: '本地路径',
};

/** 调度放置策略选项 */
export const PLACEMENT_STRATEGY_OPTIONS = [
  { label: 'Spread（分散，优先均衡）', value: 'spread' },
  { label: 'Binpack（聚拢，优先装满）', value: 'binpack' },
];

/** 模型来源选项 */
export const SOURCE_OPTIONS = [
  { label: 'HuggingFace', value: 'huggingface' },
  { label: 'ModelScope', value: 'model_scope' },
  { label: '本地路径', value: 'local_path' },
];

/** 访问策略选项 */
export const ACCESS_POLICY_OPTIONS = [
  { label: '公开', value: 'public' },
  { label: '登录可用', value: 'authed' },
  { label: '指定主体', value: 'allowed_principals' },
];

/** 模型库排序方式（浏览更多模型抽屉） */
export const MODEL_LIBRARY_SORT_OPTIONS = [
  { label: '趋势', value: 'trending' },
  { label: '点赞量', value: 'likes' },
  { label: '下载量', value: 'downloads' },
  { label: '更新时间', value: 'updated' },
];

/** 量化方式筛选（前端对 spec.quantization 过滤） */
export const QUANTIZATION_OPTIONS = [
  { label: 'FP8', value: 'FP8' },
  { label: 'AWQ', value: 'AWQ' },
  { label: 'GPTQ', value: 'GPTQ' },
];

/** 推理后端选项（对齐 GPUStack BackendEnum） */
export const INFERENCE_BACKEND_OPTIONS = [
  { label: 'vLLM', value: 'vLLM' },
  { label: 'SGLang', value: 'SGLang' },
  { label: 'MindIE', value: 'MindIE' },
  { label: 'VoxBox', value: 'VoxBox' },
  { label: '自定义 (Custom)', value: 'Custom' },
];

/** 内置推理后端（支持扩展 KV 缓存） */
export const BUILT_IN_BACKENDS = ['vLLM', 'SGLang'];

/** 模型类别（对齐 GPUStack CategoryEnum） */
export const MODEL_CATEGORY_OPTIONS = [
  { label: '大语言模型', value: 'llm' },
  { label: '向量', value: 'embedding' },
  { label: '图像', value: 'image' },
  { label: '重排', value: 'reranker' },
  { label: '语音识别', value: 'speech_to_text' },
  { label: '语音合成', value: 'text_to_speech' },
  { label: '未知', value: 'unknown' },
];

/** 节点选择器：键固定下拉（来自 GPUStack worker 内建 labels） */
export const NODE_SELECTOR_KEY_OPTIONS = [
  { label: '操作系统 (os)', value: 'os' },
  { label: '架构 (arch)', value: 'arch' },
  { label: '节点名 (worker-name)', value: 'worker-name' },
];

/** 节点选择器：键 -> 候选值（worker-name 的值由 workerList 动态填充） */
export const NODE_SELECTOR_VALUE_OPTIONS: Record<string, { label: string; value: string }[]> = {
  os: [
    { label: 'linux', value: 'linux' },
    { label: 'windows', value: 'windows' },
    { label: 'darwin', value: 'darwin' },
  ],
  arch: [
    { label: 'amd64', value: 'amd64' },
    { label: 'arm64', value: 'arm64' },
    { label: 'arm', value: 'arm' },
    { label: '386', value: '386' },
  ],
  'worker-name': [],
};

/** 调度方式 */
export const SCHEDULE_MODE_OPTIONS = [
  { label: '自动', value: 'auto' },
  { label: '手动', value: 'manual' },
];

/** 调度放置策略（沿用 GPUStack PlacementStrategyEnum） */
export const PLACEMENT_STRATEGY_SELECT_OPTIONS = [
  { label: 'Spread（分散，优先均衡）', value: 'spread' },
  { label: 'Binpack（聚拢，优先装满）', value: 'binpack' },
];

/** GPU 显存/利用率仪表盘颜色阈值 */
export function gaugeColor(rate: number): string {
  if (rate >= 90) return '#cf1322';
  if (rate >= 70) return '#d48806';
  return '#3f8600';
}

/** 字节 -> 人类可读（GB/MB），用于显存/内存展示 */
export function formatBytes(bytes?: number | null): string {
  if (bytes == null) return '-';
  const gb = bytes / 1024 / 1024 / 1024;
  if (gb >= 1) return `${gb.toFixed(1)} GB`;
  const mb = bytes / 1024 / 1024;
  return `${mb.toFixed(0)} MB`;
}
