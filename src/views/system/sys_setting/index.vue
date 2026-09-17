<template>
  <div class="sys-setting-page">
    <header class="page-heading">
      <div>
        <h1>系统设置</h1>
        <p>配置系统内部使用的默认大语言模型和向量模型。</p>
      </div>
      <a-tag color="blue">平台级配置</a-tag>
    </header>

    <a-spin :spinning="loading">
      <a-form ref="formRef" :model="formState" :rules="rules" layout="vertical">
        <div class="setting-grid">
          <section class="setting-card">
            <div class="card-heading">
              <span class="card-icon llm"><RobotOutlined /></span>
              <div>
                <h2>默认 LLM 模型</h2>
                <p>用于系统内部的对话、总结与智能处理。</p>
              </div>
              <span :class="['connection-status', llmStatus]">{{ statusText(llmStatus) }}</span>
            </div>

            <a-form-item label="模型名称" :name="['llm', 'model']">
              <a-input v-model:value="formState.llm.model" placeholder="例如：qwen-plus" allow-clear />
            </a-form-item>
            <a-form-item label="API 地址" :name="['llm', 'base_url']">
              <a-input v-model:value="formState.llm.base_url" placeholder="例如：https://api.example.com/v1" allow-clear />
            </a-form-item>
            <a-form-item label="API Key" :name="['llm', 'api_key']">
              <a-input-password v-model:value="formState.llm.api_key" autocomplete="new-password" :placeholder="llmKeyPlaceholder" />
            </a-form-item>

            <div class="card-footer">
              <span class="test-message">{{ llmTestMessage }}</span>
              <a-button html-type="button" :loading="testing === 'llm'" @click="handleTest('llm')">
                <template #icon><ApiOutlined /></template>
                测试连接
              </a-button>
            </div>
          </section>

          <section class="setting-card">
            <div class="card-heading">
              <span class="card-icon embedding"><ClusterOutlined /></span>
              <div>
                <h2>默认向量模型</h2>
                <p>用于知识检索、语义匹配与向量索引。</p>
              </div>
              <span :class="['connection-status', embeddingStatus]">{{ statusText(embeddingStatus) }}</span>
            </div>

            <a-form-item label="模型名称" :name="['embedding', 'model']">
              <a-input v-model:value="formState.embedding.model" placeholder="例如：text-embedding-v3" allow-clear />
            </a-form-item>
            <a-form-item label="API 地址" :name="['embedding', 'base_url']">
              <a-input v-model:value="formState.embedding.base_url" placeholder="例如：https://api.example.com/v1" allow-clear />
            </a-form-item>
            <div class="field-grid">
              <a-form-item label="API Key" :name="['embedding', 'api_key']">
                <a-input-password v-model:value="formState.embedding.api_key" autocomplete="new-password" :placeholder="embeddingKeyPlaceholder" />
              </a-form-item>
              <a-form-item label="向量维度" :name="['embedding', 'dimension']">
                <a-input-number v-model:value="formState.embedding.dimension" :min="1" :max="65536" placeholder="例如：1024" style="width: 100%" />
              </a-form-item>
            </div>

            <div class="card-footer">
              <span class="test-message">{{ embeddingTestMessage }}</span>
              <a-button html-type="button" :loading="testing === 'embedding'" @click="handleTest('embedding')">
                <template #icon><ApiOutlined /></template>
                测试连接
              </a-button>
            </div>
          </section>
        </div>

        <section class="setting-card ocr-card">
          <div class="card-heading">
            <span class="card-icon ocr"><FileSearchOutlined /></span>
            <div>
              <h2>文档 / 图片 OCR</h2>
              <p>非视觉主模型时，图片/扫描件在上传阶段抽取文字后再交给主模型；视觉模型直接看图，无需 OCR。</p>
            </div>
            <span :class="['connection-status', ocrStatus]">{{ ocr.enabled ? statusText(ocrStatus) : '未启用' }}</span>
          </div>

          <div class="ocr-body">
            <a-form-item label="启用 OCR">
              <a-switch v-model:checked="ocr.enabled" />
            </a-form-item>
            <a-form-item label="识别策略">
              <a-select v-model:value="ocr.strategy" :options="ocrStrategyOptions" style="max-width: 280px" />
            </a-form-item>

            <template v-if="ocr.strategy === 'custom_endpoint'">
              <a-form-item label="OCR 端点地址">
                <a-input v-model:value="ocr.endpointUrl" placeholder="例如：http://ocr-service:8000/ocr" allow-clear />
              </a-form-item>
              <a-form-item label="端点 API Key">
                <a-input-password v-model:value="ocr.apiKey" autocomplete="new-password" :placeholder="ocrKeyPlaceholder" />
              </a-form-item>
            </template>
            <template v-else-if="ocr.strategy === 'multimodal_model'">
              <a-form-item label="视觉模型名称">
                <a-input v-model:value="ocr.model" placeholder="例如：qwen-vl-max / glm-4v / gpt-4o" allow-clear />
              </a-form-item>
              <a-form-item label="模型 Base URL">
                <a-input
                  v-model:value="ocr.visionBaseUrl"
                  placeholder="OpenAI 兼容地址，如 https://dashscope.aliyuncs.com/compatible-mode/v1；留空=走平台 New API 网关"
                  allow-clear
                />
              </a-form-item>
              <a-form-item label="模型 API Key">
                <a-input-password
                  v-model:value="ocr.visionApiKey"
                  autocomplete="new-password"
                  :placeholder="ocrVisionKeyPlaceholder"
                />
              </a-form-item>
              <a-form-item label="识别提示词">
                <a-textarea
                  v-model:value="ocr.visionPrompt"
                  :rows="3"
                  placeholder="留空=默认：完整描述图片内容并转录文字，供主对话模型据此作答。可自定义（如只提取表格数据）"
                  allow-clear
                />
              </a-form-item>
              <p class="ocr-hint">上传图片 → 视觉模型看图识别成文字 → 随附件传给主对话模型作答（主模型无需支持图片）。</p>
            </template>
          </div>

          <div class="card-footer">
            <span class="test-message">{{ ocrTestMessage }}</span>
            <a-button
              html-type="button"
              :loading="testingOcr"
              :disabled="ocr.strategy === 'none'"
              @click="handleTestOcr"
            >
              <template #icon><ApiOutlined /></template>
              测试连接
            </a-button>
          </div>
        </section>

        <footer class="page-actions">
          <span><InfoCircleOutlined /> API Key 仅脱敏展示，页面不会回显明文。</span>
          <a-button html-type="button" type="primary" size="large" :loading="saving" @click="handleSave">
            <template #icon><SaveOutlined /></template>
            保存配置
          </a-button>
        </footer>
      </a-form>
    </a-spin>
  </div>
</template>

<script setup lang="ts">
  defineOptions({ name: 'SystemSettingPage' });

  import { computed, onMounted, reactive, ref } from 'vue';
  import type { FormInstance, Rule } from 'ant-design-vue/es/form';
  import { ApiOutlined, ClusterOutlined, FileSearchOutlined, InfoCircleOutlined, RobotOutlined, SaveOutlined } from '@ant-design/icons-vue';
  import { useMessage } from '/@/hooks/web/useMessage';
  import { getSystemModelSetting, saveSystemModelSetting, testSystemModel, type SystemModelSetting } from './sysSetting.api';
  import { getOcrConfig, saveOcrConfig, testOcrConfig } from '/@/views/newapi/ocr/ocr.api';

  type ModelType = 'llm' | 'embedding';
  type TestStatus = 'success' | 'failed' | '';

  const { createMessage } = useMessage();
  const formRef = ref<FormInstance>();
  const loading = ref(false);
  const saving = ref(false);
  const testing = ref<ModelType | ''>('');
  const llmStatus = ref<TestStatus>('');
  const embeddingStatus = ref<TestStatus>('');
  const llmTestMessage = ref('');
  const embeddingTestMessage = ref('');
  const maskedKeys = reactive({ llm: '', embedding: '' });

  const formState = reactive<SystemModelSetting>({
    llm: { model: '', base_url: '', api_key: '' },
    embedding: { model: '', base_url: '', api_key: '', dimension: null },
  });

  // OCR 平台配置（独立后端 /agent-api/platform-config/ocr，随本页「保存配置」一并落库）
  const ocr = reactive({
    enabled: false,
    strategy: 'none',
    endpointUrl: '',
    apiKey: '',
    model: '',
    visionBaseUrl: '',
    visionApiKey: '',
    visionPrompt: '',
  });
  const ocrSecrets = reactive<{ apiKey: boolean; visionApiKey: boolean }>({ apiKey: false, visionApiKey: false });
  const ocrStatus = ref<TestStatus>('');
  const ocrTestMessage = ref('');
  const testingOcr = ref(false);
  const ocrStrategyOptions = [
    { label: '关闭', value: 'none' },
    { label: '自建 OCR 端点', value: 'custom_endpoint' },
    { label: '多模态模型识别', value: 'multimodal_model' },
  ];
  const ocrKeyPlaceholder = computed(() => (ocrSecrets.apiKey ? '已配置（留空则不修改）' : '可选'));
  const ocrVisionKeyPlaceholder = computed(() =>
    ocrSecrets.visionApiKey ? '已配置（留空则不修改）' : '填了 Base URL 才需要',
  );

  const requiredText = (label: string): Rule => ({ required: true, whitespace: true, message: '请输入' + label, trigger: 'blur' });

  const urlRule: Rule = {
    validator: async (_rule, value) => {
      if (!value) return Promise.resolve();
      try {
        const url = new URL(value);
        if (!['http:', 'https:'].includes(url.protocol)) throw new Error();
        return Promise.resolve();
      } catch {
        return Promise.reject('请输入有效的 HTTP 或 HTTPS 地址');
      }
    },
    trigger: 'blur',
  };

  const keyRule = (type: ModelType): Rule => ({
    validator: async (_rule, value) => (value || maskedKeys[type] ? Promise.resolve() : Promise.reject('请输入 API Key')),
    trigger: 'blur',
  });

  const rules = {
    llm: {
      model: [requiredText('模型名称')],
      base_url: [requiredText('API 地址'), urlRule],
      api_key: [keyRule('llm')],
    },
    embedding: {
      model: [requiredText('模型名称')],
      base_url: [requiredText('API 地址'), urlRule],
      api_key: [keyRule('embedding')],
      dimension: [{ required: true, type: 'number', min: 1, message: '请输入有效的向量维度', trigger: 'change' }],
    },
  };

  const llmKeyPlaceholder = computed(() => (maskedKeys.llm ? '已配置 ' + maskedKeys.llm + '，留空则不修改' : '请输入 API Key'));
  const embeddingKeyPlaceholder = computed(() => (maskedKeys.embedding ? '已配置 ' + maskedKeys.embedding + '，留空则不修改' : '请输入 API Key'));

  function statusText(status: TestStatus) {
    return status === 'success' ? '已连接' : status === 'failed' ? '连接失败' : '未测试';
  }

  async function loadSetting() {
    loading.value = true;
    try {
      const data = await getSystemModelSetting();
      Object.assign(formState.llm, { model: data?.llm?.model || '', base_url: data?.llm?.base_url || '', api_key: '' });
      Object.assign(formState.embedding, {
        model: data?.embedding?.model || '',
        base_url: data?.embedding?.base_url || '',
        api_key: '',
        dimension: data?.embedding?.dimension || null,
      });
      maskedKeys.llm = data?.llm?.api_key_masked || '';
      maskedKeys.embedding = data?.embedding?.api_key_masked || '';
      llmStatus.value = data?.llm?.test_status || '';
      embeddingStatus.value = data?.embedding?.test_status || '';
      llmTestMessage.value = data?.llm?.test_message || '';
      embeddingTestMessage.value = data?.embedding?.test_message || '';
    } catch (error) {
      console.error('加载系统默认模型配置失败', error);
    } finally {
      loading.value = false;
    }
    // OCR 配置独立后端，单独加载：失败不影响模型卡片
    try {
      const o: any = await getOcrConfig();
      ocr.enabled = Boolean(o?.enabled);
      ocr.strategy = o?.strategy || 'none';
      ocr.endpointUrl = o?.endpointUrl || '';
      ocr.model = o?.model || '';
      ocr.visionBaseUrl = o?.visionBaseUrl || '';
      ocr.visionPrompt = o?.visionPrompt || '';
      ocr.apiKey = '';
      ocr.visionApiKey = '';
      ocrSecrets.apiKey = Boolean(o?.secrets_set?.apiKey);
      ocrSecrets.visionApiKey = Boolean(o?.secrets_set?.visionApiKey);
    } catch (error) {
      console.error('加载 OCR 配置失败', error);
    }
  }

  function validateOcr(): boolean {
    if (!ocr.enabled) return true;
    if (ocr.strategy === 'none') {
      createMessage.warning('已启用 OCR，请先选择识别策略');
      return false;
    }
    if (ocr.strategy === 'custom_endpoint' && !ocr.endpointUrl.trim()) {
      createMessage.warning('请填写 OCR 端点地址');
      return false;
    }
    if (ocr.strategy === 'multimodal_model' && !ocr.model.trim()) {
      createMessage.warning('请填写多模态模型名称');
      return false;
    }
    return true;
  }

  async function handleTestOcr() {
    testingOcr.value = true;
    ocrTestMessage.value = '';
    try {
      const result: any = await testOcrConfig({ ...ocr });
      const ok = result?.status === 'success';
      ocrStatus.value = ok ? 'success' : result?.status === 'info' ? '' : 'failed';
      ocrTestMessage.value = result?.message || (ok ? '连接成功' : '测试失败');
      ok ? createMessage.success('OCR 连接成功') : createMessage.error(ocrTestMessage.value);
    } catch (error: any) {
      ocrStatus.value = 'failed';
      ocrTestMessage.value = error?.message || '测试失败';
      createMessage.error(ocrTestMessage.value);
    } finally {
      testingOcr.value = false;
    }
  }

  async function validateSection(type: ModelType) {
    const fields =
      type === 'llm'
        ? [['llm', 'model'], ['llm', 'base_url'], ['llm', 'api_key']]
        : [['embedding', 'model'], ['embedding', 'base_url'], ['embedding', 'api_key'], ['embedding', 'dimension']];
    await formRef.value?.validateFields(fields);
  }

  async function handleTest(type: ModelType) {
    try {
      await validateSection(type);
      testing.value = type;
      const result = await testSystemModel(type, formState[type]);

      if (type === 'llm') {
        llmStatus.value = 'success';
        llmTestMessage.value = result.message || '连接成功';
      } else {
        embeddingStatus.value = 'success';
        embeddingTestMessage.value = result.dimension ? '连接成功，服务返回 ' + result.dimension + ' 维向量' : result.message || '连接成功';
      }
      createMessage.success(type === 'llm' ? 'LLM 模型连接成功' : '向量模型连接成功');
    } catch (error: any) {
      if (error?.errorFields) {
        createMessage.warning(type === 'llm' ? '请先完善 LLM 模型配置' : '请先完善向量模型配置');
        return;
      }
      if (type === 'llm') {
        llmStatus.value = 'failed';
        llmTestMessage.value = error?.message || '连接失败';
      } else {
        embeddingStatus.value = 'failed';
        embeddingTestMessage.value = error?.message || '连接失败';
      }
      createMessage.error(error?.message || '连接测试失败');
    } finally {
      testing.value = '';
    }
  }

  async function handleSave() {
    try {
      await formRef.value?.validate();
      if (!validateOcr()) return;
      saving.value = true;
      await saveSystemModelSetting(formState);
      await saveOcrConfig({ ...ocr });
      formState.llm.api_key = '';
      formState.embedding.api_key = '';
      ocr.apiKey = '';
      ocr.visionApiKey = '';
      await loadSetting();
    } catch (error: any) {
      if (!error?.errorFields) createMessage.error(error?.message || '保存失败');
    } finally {
      saving.value = false;
    }
  }

  onMounted(loadSetting);
</script>

<style scoped lang="less">
  .sys-setting-page { min-height: 100%; padding: 24px; background: #f5f7fa; }
  .page-heading { display: flex; align-items: flex-start; justify-content: space-between; max-width: 1180px; margin: 0 auto 20px; }
  .page-heading h1 { margin: 0; color: #172033; font-size: 24px; font-weight: 700; line-height: 1.4; }
  .page-heading p { margin: 6px 0 0; color: #7b8497; font-size: 14px; }
  .setting-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; max-width: 1180px; margin: 0 auto; }
  .setting-card { padding: 24px; border: 1px solid #e8ebf1; border-radius: 12px; background: #fff; box-shadow: 0 8px 24px rgb(15 23 42 / 4%); }
  .card-heading { display: grid; grid-template-columns: auto 1fr auto; gap: 12px; align-items: center; margin-bottom: 24px; }
  .card-heading h2 { margin: 0; color: #172033; font-size: 17px; font-weight: 700; }
  .card-heading p { margin: 4px 0 0; color: #8b93a4; font-size: 12px; }
  .card-icon { display: inline-flex; align-items: center; justify-content: center; width: 42px; height: 42px; border-radius: 10px; font-size: 20px; }
  .card-icon.llm { color: #315efb; background: #eef2ff; }
  .card-icon.embedding { color: #1677ff; background: #eaf5ff; }
  .card-icon.ocr { color: #b45309; background: #fff3e6; }
  .ocr-card { max-width: 1180px; margin: 20px auto 0; }
  .ocr-body { max-width: 560px; }
  .ocr-hint { margin: -4px 0 8px; color: #8b93a4; font-size: 12px; }
  .connection-status { color: #98a0b2; font-size: 12px; white-space: nowrap; }
  .connection-status.success { color: #16a36a; }
  .connection-status.failed { color: #e5484d; }
  .field-grid { display: grid; grid-template-columns: minmax(0, 1fr) 140px; gap: 14px; }
  .card-footer { display: flex; gap: 16px; align-items: center; justify-content: space-between; min-height: 32px; padding-top: 4px; }
  .test-message { overflow: hidden; color: #8b93a4; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
  .page-actions { display: flex; align-items: center; justify-content: space-between; max-width: 1180px; margin: 20px auto 0; padding: 18px 20px; border: 1px solid #e8ebf1; border-radius: 12px; background: #fff; }
  .page-actions span { color: #7b8497; font-size: 13px; }
  :deep(.ant-form-item-label > label) { color: #3c4558; font-weight: 600; }
  :deep(.ant-input), :deep(.ant-input-affix-wrapper), :deep(.ant-input-number) { border-radius: 7px; }
  @media (max-width: 900px) { .setting-grid { grid-template-columns: 1fr; } }
  @media (max-width: 576px) { .sys-setting-page { padding: 16px; } .page-heading, .page-actions { gap: 12px; align-items: flex-start; } .page-actions { flex-direction: column; } .field-grid { grid-template-columns: 1fr; gap: 0; } }
  @media (prefers-reduced-motion: reduce) { *, *::before, *::after { scroll-behavior: auto !important; transition: none !important; animation: none !important; } }
</style>
