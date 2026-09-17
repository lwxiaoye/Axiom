<!-- eslint-disable vue/multi-word-component-names -->
<template>
  <div class="p-4">
    <a-card title="OCR 文档识别配置" :bordered="false">
      <template #extra>
        <a-tag :color="formData.enabled ? 'green' : 'default'">
          {{ formData.enabled ? '已启用' : '已关闭' }}
        </a-tag>
      </template>

      <a-alert
        class="mb-4"
        type="info"
        show-icon
        message="上传图片时，由视觉模型「看图」把内容识别成文字，再随附件回传给主对话模型作答——这样即使主模型不支持图片也能理解图片。可接自建 OCR 端点，或配置视觉模型（独立 Base URL / API Key / 模型名，留空则走平台 New API 网关）。默认关闭。"
      />

      <a-form :model="formData" :label-col="{ span: 6 }" :wrapper-col="{ span: 14 }">
        <a-form-item label="启用 OCR">
          <a-switch v-model:checked="formData.enabled" />
        </a-form-item>

        <a-form-item label="识别策略">
          <a-select v-model:value="formData.strategy" :options="strategyOptions" />
        </a-form-item>

        <template v-if="formData.strategy === 'custom_endpoint'">
          <a-form-item label="OCR 端点地址">
            <a-input v-model:value="formData.endpointUrl" placeholder="例如: http://ocr-service:8000/ocr" />
          </a-form-item>
          <a-form-item label="端点 API Key">
            <a-input-password v-model:value="formData.apiKey" :placeholder="secretPlaceholder('apiKey', '可选')" />
          </a-form-item>
        </template>

        <template v-else-if="formData.strategy === 'multimodal_model'">
          <a-form-item label="视觉模型名称">
            <a-input v-model:value="formData.model" placeholder="例如: qwen-vl-max / glm-4v / gpt-4o" />
          </a-form-item>
          <a-form-item label="模型 Base URL">
            <a-input
              v-model:value="formData.visionBaseUrl"
              placeholder="OpenAI 兼容地址，如 https://dashscope.aliyuncs.com/compatible-mode/v1；留空=走平台 New API 网关"
            />
          </a-form-item>
          <a-form-item label="模型 API Key">
            <a-input-password
              v-model:value="formData.visionApiKey"
              :placeholder="secretPlaceholder('visionApiKey', '填了 Base URL 才需要')"
            />
          </a-form-item>
          <a-form-item label="识别提示词">
            <a-textarea
              v-model:value="formData.visionPrompt"
              :rows="3"
              placeholder="留空=默认：完整描述图片内容并转录文字，供主对话模型据此作答。可自定义（如只提取表格数据）"
            />
          </a-form-item>
          <a-form-item :wrapper-col="{ offset: 6, span: 14 }">
            <span class="text-gray-500 text-xs">
              流程：上传图片 → 视觉模型看图识别成文字 → 随附件传给主对话模型作答。模型须支持图片输入。
            </span>
          </a-form-item>
        </template>

        <a-form-item label="测试结果" v-if="testResult">
          <a-tag :color="testColor">{{ testResult.status }}</a-tag>
          <span class="ml-2 text-gray-500 text-xs">{{ testResult.message }}</span>
        </a-form-item>

        <a-form-item :wrapper-col="{ offset: 6, span: 14 }">
          <a-space>
            <a-button type="primary" :loading="saving" @click="handleSave">保存配置</a-button>
            <a-button :loading="testing" :disabled="formData.strategy === 'none'" @click="handleTest">测试</a-button>
          </a-space>
        </a-form-item>
      </a-form>
    </a-card>
  </div>
</template>

<script lang="ts" name="OcrConfig" setup>
  import { ref, computed, onMounted } from 'vue';
  import { useMessage } from '/@/hooks/web/useMessage';
  import { getOcrConfig, saveOcrConfig, testOcrConfig } from './ocr.api';

  const { createMessage } = useMessage();

  const saving = ref(false);
  const testing = ref(false);
  const secretsSet = ref<Record<string, boolean>>({});
  const testResult = ref<any>(null);

  const formData = ref({
    enabled: false,
    strategy: 'none',
    endpointUrl: '',
    apiKey: '',
    model: '',
    visionBaseUrl: '',
    visionApiKey: '',
    visionPrompt: '',
  });

  const strategyOptions = [
    { label: '关闭', value: 'none' },
    { label: '自建 OCR 端点', value: 'custom_endpoint' },
    { label: '多模态模型识别', value: 'multimodal_model' },
  ];

  const testColor = computed(() => {
    const s = testResult.value?.status;
    if (s === 'success') return 'green';
    if (s === 'info') return 'blue';
    return 'red';
  });

  function secretPlaceholder(field: string, fallback = '请输入 API Key') {
    return secretsSet.value[field] ? '已配置（留空则不修改）' : fallback;
  }

  async function loadConfig() {
    try {
      const data = await getOcrConfig();
      secretsSet.value = data.secrets_set || {};
      formData.value.enabled = Boolean(data.enabled);
      formData.value.strategy = data.strategy || 'none';
      formData.value.endpointUrl = data.endpointUrl || '';
      formData.value.model = data.model || '';
      formData.value.visionBaseUrl = data.visionBaseUrl || '';
      formData.value.visionPrompt = data.visionPrompt || '';
      formData.value.apiKey = '';
      formData.value.visionApiKey = '';
    } catch (e) {
      console.error('加载 OCR 配置失败', e);
    }
  }

  async function handleSave() {
    saving.value = true;
    try {
      const data = await saveOcrConfig(formData.value);
      secretsSet.value = data.secrets_set || {};
      formData.value.apiKey = '';
      formData.value.visionApiKey = '';
      createMessage.success('保存成功');
    } catch (e: any) {
      createMessage.error(e.message || '保存失败');
    } finally {
      saving.value = false;
    }
  }

  async function handleTest() {
    testing.value = true;
    testResult.value = null;
    try {
      testResult.value = await testOcrConfig(formData.value);
    } catch (e: any) {
      testResult.value = { status: 'failed', message: e.message || '测试失败' };
    } finally {
      testing.value = false;
    }
  }

  onMounted(loadConfig);
</script>
