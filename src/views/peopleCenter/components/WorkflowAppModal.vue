<template>
  <a-modal
    v-model:open="open"
    :width="760"
    :title="modalTitle"
    :footer="null"
    :destroyOnClose="true"
    :maskClosable="false"
    wrapClassName="workflow-app-modal"
    @cancel="close"
  >
    <div class="workflow-app-modal-body">
      <a-form ref="formRef" :model="form" :rules="rules" layout="vertical">
        <div v-if="!form.id" :class="['kind-cards', { 'three-col': kindOptions.length > 2 }]">
          <button
            v-for="item in kindOptions"
            :key="item.value"
            type="button"
            :class="['kind-card', { active: form.aiAppType === item.value }]"
            @click="form.aiAppType = item.value"
          >
            <span :class="['wb-tile', item.value]">{{ item.shortName }}</span>
            <strong>{{ item.label }}</strong>
            <em>{{ item.description }}</em>
          </button>
        </div>

        <div :class="['form-grid', { 'single-col': isToolContext }]">
          <a-form-item :label="nameLabel" name="name">
            <a-input v-model:value="form.name" :placeholder="namePlaceholder" maxlength="128" />
          </a-form-item>
          <a-form-item v-if="!isToolContext" :label="categoryLabel" name="appCategory">
            <JDictSelectTag
              v-model:value="form.appCategory"
              dictCode="app_category"
              type="select"
              :placeholder="categoryPlaceholder"
              showSearch
              allowClear
              :showChooseOption="false"
              :onlySearchByLabel="true"
              :style="{ width: '100%' }"
            />
          </a-form-item>
        </div>

        <a-form-item v-if="!isToolContext" label="智能体图标" name="appIcon">
          <div class="agent-icon-upload">
            <a-upload
              accept="image/*"
              list-type="picture-card"
              :max-count="1"
              :show-upload-list="false"
              :before-upload="beforeIconUpload"
              :disabled="iconUploading"
              @change="handleIconUploadChange"
            >
              <div class="agent-icon-upload-card">
                <LoadingOutlined v-if="iconUploading" />
                <img
                  v-else-if="form.appIcon && !iconPreviewFailed"
                  :src="iconPreviewUrl"
                  alt="智能体图标预览"
                  @load="iconPreviewFailed = false"
                  @error="iconPreviewFailed = true"
                />
                <template v-else>
                  <UploadOutlined />
                  <span>{{ form.appIcon ? '已上传' : '上传图标' }}</span>
                </template>
              </div>
            </a-upload>
            <a-button v-if="form.appIcon" type="link" size="small" :disabled="iconUploading" @click="clearIcon">移除</a-button>
            <span class="agent-icon-tip">支持 JPG、PNG、WebP、GIF，建议 1:1，2MB 以内。</span>
          </div>
        </a-form-item>

        <a-form-item :label="descriptionLabel" name="description">
          <a-textarea v-model:value="form.description" :placeholder="descriptionPlaceholder" :rows="4" maxlength="512" />
        </a-form-item>

        <div v-if="props.context === 'agent'" class="publish-note">
          <strong>发布规则</strong>
          <p>保存配置只写入 AI 应用草稿；点击发布后才会同步创建应用门户记录，并默认保持未启用，等待有权限的人启用。</p>
        </div>
      </a-form>
    </div>

    <div class="workflow-app-modal-footer">
      <a-button @click="close">取消</a-button>
      <a-button type="primary" :loading="saving" :disabled="iconUploading" @click="saveBase">保存</a-button>
    </div>
  </a-modal>
</template>

<script setup lang="ts">
import { LoadingOutlined, UploadOutlined } from '@ant-design/icons-vue';
import { message } from 'ant-design-vue';
import { computed, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';
import type { FormInstance } from 'ant-design-vue';
import { uploadImg } from '/@/api/sys/upload';
import { JDictSelectTag } from '/@/components/Form';
import { getFileAccessHttpUrl } from '/@/utils/common/compUtils';
import {
  createWorkflowApp,
  updateWorkflowApp,
  type AiWorkflowApp,
} from '../../workflow/api/workflow.api';
import { getAiAppRoute, type AiAppKind } from '../../workflow/shared/agentApp';

const props = withDefaults(
  defineProps<{
    /** agent：创建智能体（简易/对话智能体/工作流）；tool：创建工具（工作流工具/HTTP 工具集） */
    context?: 'agent' | 'tool';
  }>(),
  { context: 'agent' }
);

const emit = defineEmits<{
  (e: 'success', app?: AiWorkflowApp): void;
}>();

const router = useRouter();

const open = ref(false);
const saving = ref(false);
const iconUploading = ref(false);
const iconPreviewFailed = ref(false);
const formRef = ref<FormInstance>();

const form = reactive<Partial<AiWorkflowApp>>({
  aiAppType: 'workflow',
  name: '',
  description: '',
  appCategory: undefined,
  appIcon: '',
  configJson: '{}',
});

/** 应用类型卡片，对齐 FastGPT 工作台（v1.11 §10.5.2，2026-07-02 定：对话智能体只做 ，排除 simple） */
const ALL_KIND_OPTIONS = [
  {
    value: 'chatAgent',
    label: '对话 Agent',
    shortName: 'AI',
    description: '模型 + 提示词 + 技能 + 工具 + 知识库，自主决定调用。',
    context: 'agent',
  },
  {
    value: 'workflow',
    label: '工作流',
    shortName: 'WF',
    description: '通过画布编排节点、工具和知识库。',
    context: 'agent',
  },
  {
    value: 'workflowTool',
    label: '工作流工具',
    shortName: 'WT',
    description: '画布编排、声明入参出参，可被其他应用当工具调用。',
    context: 'tool',
  },
  {
    value: 'httpToolSet',
    label: 'HTTP 工具',
    shortName: 'API',
    description: '录入接口清单、cURL 或导入 OpenAPI，批量生成工具。',
    context: 'tool',
  },
  {
    value: 'mcpToolSet',
    label: 'MCP 工具',
    shortName: 'MCP',
    description: '连接外部 MCP Server，解析并调用其工具列表。',
    context: 'tool',
  },
] as const;

const kindOptions = computed(() => ALL_KIND_OPTIONS.filter((item) => item.context === props.context));
const isToolContext = computed(() => props.context === 'tool');
const modalTitle = computed(() => {
  if (isToolContext.value) return form.id ? '编辑工具' : '创建工具';
  return form.id ? '编辑 AI 应用' : '创建 AI 应用';
});
const nameLabel = computed(() => (isToolContext.value ? '工具名称' : '应用名称'));
const categoryLabel = computed(() => (isToolContext.value ? '工具分类' : '能力分类'));
const descriptionLabel = computed(() => (isToolContext.value ? '工具描述' : '应用描述'));
const namePlaceholder = computed(() => (isToolContext.value ? '例如：教务系统查询工具' : '例如：招生政策助手'));
const categoryPlaceholder = computed(() => (isToolContext.value ? '请选择工具分类' : '请选择能力分类'));
const descriptionPlaceholder = computed(() =>
  isToolContext.value ? '描述这个工具的调用场景和可提供的能力' : '描述这个智能体能解决什么问题'
);
const iconPreviewUrl = computed(() => getFileAccessHttpUrl(String(form.appIcon || '').trim()));

const rules = computed(() => {
  const base: Record<string, any> = {
    name: [{ required: true, message: isToolContext.value ? '请输入工具名称' : '请输入应用名称', trigger: 'blur' }],
  };
  // 工具不再需要分类和图标，仅智能体保留分类必填
  if (!isToolContext.value) {
    base.appCategory = [{ required: true, message: '请选择能力分类', trigger: 'change' }];
  }
  return base;
});

function defaultKindByContext(): AiAppKind {
  return props.context === 'tool' ? 'workflowTool' : 'chatAgent';
}

function normalizeUploadUrl(response: any): string {
  const result = response?.result ?? response?.data?.result;
  if (typeof result === 'string') return getFileAccessHttpUrl(result.trim());
  const data = result || response?.data || response || {};
  const candidates = [data.url, data.fileUrl, data.path, response?.message, response?.data?.message]
    .map((item) => String(item || '').trim())
    .filter(Boolean);
  const rawUrl = candidates.find((item) => /^https?:\/\//.test(item) || item.startsWith('/') || item.includes('.')) || '';
  return rawUrl ? getFileAccessHttpUrl(rawUrl) : '';
}

function getUploadFile(info: any): File | undefined {
  const file = info?.file?.originFileObj || info?.file;
  return file instanceof File ? file : undefined;
}

function isValidIconFile(file: File) {
  const name = String(file.name || '');
  const mime = String(file.type || '');
  const isImage = mime ? mime.startsWith('image/') : /\.(jpe?g|png|webp|gif)$/i.test(name);
  if (!isImage) {
    message.warning('请选择图片文件作为智能体图标');
    return false;
  }
  if (file.size / 1024 / 1024 > 2) {
    message.warning('智能体图标大小不能超过 2MB');
    return false;
  }
  return true;
}

function beforeIconUpload() {
  return false;
}

async function handleIconUploadChange(info: any) {
  const file = getUploadFile(info);
  if (!file || iconUploading.value || !isValidIconFile(file)) return;
  iconUploading.value = true;
  try {
    const response = await uploadImg({ file }, () => {});
    const url = normalizeUploadUrl(response);
    if (!url) throw new Error(`${file.name} 上传后未返回文件地址`);
    form.appIcon = url;
    iconPreviewFailed.value = false;
    message.success('智能体图标已上传');
    setTimeout(() => formRef.value?.clearValidate?.('appIcon'), 0);
  } catch (error: any) {
    message.error(error?.message || '智能体图标上传失败');
  } finally {
    iconUploading.value = false;
  }
}

function clearIcon() {
  form.appIcon = '';
  iconPreviewFailed.value = false;
  setTimeout(() => formRef.value?.clearValidate?.('appIcon'), 0);
}

function reset(record?: Partial<AiWorkflowApp>, defaultKind?: AiAppKind) {
  Object.assign(form, {
    id: undefined,
    appInfoId: undefined,
    aiAppType: defaultKind || defaultKindByContext(),
    name: '',
    description: '',
    appCategory: undefined,
    appIcon: '',
    configJson: '{}',
  }, record || {});
  form.aiAppType = (record?.aiAppType || defaultKind || defaultKindByContext()) as AiAppKind;
  iconPreviewFailed.value = false;
}

async function init(record?: Partial<AiWorkflowApp>, defaultKind?: AiAppKind) {
  reset(record, defaultKind);
  open.value = true;
  setTimeout(() => formRef.value?.clearValidate?.(), 0);
}

async function saveBase() {
  if (iconUploading.value) {
    message.warning('智能体图标上传中，请稍后保存');
    return;
  }
  await formRef.value?.validate();
  saving.value = true;
  try {
    const payload = {
      ...form,
      configJson: form.configJson || '{}',
    };
    const isCreate = !form.id;
    const saved = form.id ? await updateWorkflowApp(payload) : await createWorkflowApp(payload);
    emit('success', saved);
    close();
    // 与蓝本一致：创建后直接进入对应编辑器；HTTP/MCP 工具集在「我的工具」列表中继续配置
    if (isCreate && saved?.id && form.aiAppType !== 'httpToolSet' && form.aiAppType !== 'mcpToolSet') {
      router.push(getAiAppRoute({ ...saved, aiAppType: saved.aiAppType || form.aiAppType }));
    }
  } finally {
    saving.value = false;
  }
}

function close() {
  open.value = false;
}

defineExpose({ init });
</script>

<style lang="less">
.workflow-app-modal {
  .ant-modal-body {
    padding: 0;
  }
}
</style>

<style scoped lang="less">
.workflow-app-modal-body {
  padding: 18px 24px 10px;
}

.kind-cards {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 18px;

  &.three-col {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

.kind-card {
  display: grid;
  gap: 8px;
  padding: 14px;
  text-align: left;
  border: 1px solid #e8eaf0;
  border-radius: 12px;
  background: #fff;
  cursor: pointer;
  transition: border-color 0.2s ease, background 0.2s ease, box-shadow 0.2s ease;

  .wb-tile {
    width: 36px;
    height: 36px;
    border-radius: 10px;
    font-size: 11px;
    letter-spacing: 0;
  }

  strong {
    color: #111827;
    font-size: 14px;
  }

  em {
    color: #64748b;
    font-size: 12px;
    font-style: normal;
    line-height: 1.6;
  }

  &:hover {
    border-color: #d5d9e2;
  }

  &.active {
    border-color: #111827;
    background: #fafbfc;
    box-shadow: 0 10px 24px rgba(15, 23, 42, 0.08);
  }
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;

  &.single-col {
    grid-template-columns: minmax(0, 1fr);
  }
}

.agent-icon-upload {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  min-height: 76px;

  :deep(.ant-upload-picture-card-wrapper) {
    width: auto;
  }

  :deep(.ant-upload.ant-upload-select-picture-card) {
    width: 72px;
    height: 72px;
    margin: 0;
    overflow: hidden;
    border-radius: 8px;
  }
}

.agent-icon-upload-card {
  display: flex;
  width: 100%;
  height: 100%;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 6px;
  color: #64748b;
  font-size: 12px;
  line-height: 1;

  img {
    display: block;
    width: 100%;
    height: 100%;
    object-fit: cover;
  }
}

.agent-icon-tip {
  color: #94a3b8;
  font-size: 12px;
}

.publish-note {
  padding: 12px 14px;
  border: 1px solid #eceef3;
  border-radius: 10px;
  background: #fafbfc;

  strong {
    color: #111827;
    font-size: 13px;
  }

  p {
    margin: 6px 0 0;
    color: #64748b;
    font-size: 12px;
    line-height: 1.7;
  }
}

.workflow-app-modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 12px 24px 18px;
  border-top: 1px solid #edf2f7;
}
</style>
