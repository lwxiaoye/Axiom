<!-- eslint-disable vue/multi-word-component-names -->
<template>
  <div class="p-4">
    <a-card title="Embedding 检索配置" :bordered="false">
      <a-form :model="formData" :label-col="{ span: 6 }" :wrapper-col="{ span: 14 }">
        <a-form-item label="模型名称">
          <a-input v-model:value="formData.model" placeholder="例如: text-embedding-v3" />
        </a-form-item>
        <a-form-item label="Base URL">
          <a-input v-model:value="formData.base_url" placeholder="例如: https://api.openai.com/v1" />
        </a-form-item>
        <a-form-item label="API Key">
          <a-input-password v-model:value="formData.api_key" :placeholder="apiKeyPlaceholder" />
        </a-form-item>
        <a-form-item label="当前维度">
          <span>{{ configData.dimension || '-' }}</span>
        </a-form-item>
        <a-form-item label="测试状态">
          <a-tag :color="testStatusColor">{{ testStatusText }}</a-tag>
          <span v-if="configData.test_message" class="ml-2 text-gray-500 text-xs">{{ configData.test_message }}</span>
        </a-form-item>
        <a-form-item label="最后测试">
          <span>{{ configData.last_test_time || '-' }}</span>
        </a-form-item>
        <a-form-item :wrapper-col="{ offset: 6, span: 14 }">
          <a-space>
            <a-button type="primary" :loading="saving" @click="handleSave">保存配置</a-button>
            <a-button :loading="testing" @click="handleTest">测试连接</a-button>
            <a-button type="primary" danger :loading="reindexing" :disabled="!canReindex" @click="handleReindex">
              立即全量向量化
            </a-button>
          </a-space>
        </a-form-item>
      </a-form>
    </a-card>

    <a-card v-if="reindexJob" title="索引进度" class="mt-4" :bordered="false">
      <a-descriptions :column="2">
        <a-descriptions-item label="状态">
          <a-tag :color="reindexJob.status === 'completed' ? 'green' : reindexJob.status === 'failed' ? 'red' : 'blue'">
            {{ reindexJob.status }}
          </a-tag>
        </a-descriptions-item>
        <a-descriptions-item label="已索引">{{ reindexJob.indexed || 0 }}</a-descriptions-item>
        <a-descriptions-item label="失败">{{ reindexJob.failed || 0 }}</a-descriptions-item>
        <a-descriptions-item label="开始时间">{{ reindexJob.started_at }}</a-descriptions-item>
      </a-descriptions>
    </a-card>
  </div>
</template>

<script lang="ts" name="EmbeddingConfig" setup>
  import { ref, computed, onMounted } from 'vue';
  import { useMessage } from '/@/hooks/web/useMessage';
  import { getConfig, saveConfig, testConfig, startReindex, getReindexStatus } from './embedding.api';

  const { createMessage } = useMessage();

  const saving = ref(false);
  const testing = ref(false);
  const reindexing = ref(false);
  const configData = ref<any>({});
  const reindexJob = ref<any>(null);

  const formData = ref({
    model: '',
    base_url: '',
    api_key: '',
  });

  const apiKeyPlaceholder = computed(() => {
    return configData.value.api_key_masked ? `已配置 ${configData.value.api_key_masked}` : '请输入 API Key';
  });

  const testStatusColor = computed(() => {
    const status = configData.value.test_status;
    if (status === 'success') return 'green';
    if (status === 'failed') return 'red';
    return 'default';
  });

  const testStatusText = computed(() => {
    const status = configData.value.test_status;
    if (status === 'success') return '测试通过';
    if (status === 'failed') return '测试失败';
    return '未测试';
  });

  const canReindex = computed(() => {
    return configData.value.test_status === 'success' && configData.value.is_active;
  });

  async function loadConfig() {
    try {
      const data = await getConfig();
      configData.value = data;
      formData.value.model = data.model || '';
      formData.value.base_url = data.base_url || '';
    } catch (e) {
      console.error('加载配置失败', e);
    }
  }

  async function handleSave() {
    if (!formData.value.model || !formData.value.base_url) {
      createMessage.warning('请填写模型名称和 Base URL');
      return;
    }
    saving.value = true;
    try {
      await saveConfig({
        model: formData.value.model,
        base_url: formData.value.base_url,
        api_key: formData.value.api_key || undefined,
      });
      createMessage.success('保存成功');
      formData.value.api_key = '';
      await loadConfig();
    } catch (e: any) {
      createMessage.error(e.message || '保存失败');
    } finally {
      saving.value = false;
    }
  }

  async function handleTest() {
    if (!formData.value.model || !formData.value.base_url) {
      createMessage.warning('请填写模型名称和 Base URL');
      return;
    }
    testing.value = true;
    try {
      const result = await testConfig({
        model: formData.value.model,
        base_url: formData.value.base_url,
        api_key: formData.value.api_key || undefined,
      });
      if (result.status === 'success') {
        if (result.persisted === false) {
          // 测的是未保存的候选配置：后端不再把维度/状态写进当前生效行（否则会改掉线上集合名导致检索失效），
          // 所以「重新索引」仍然是禁用的——这里明确告诉用户要先保存。
          createMessage.warning(`测试成功，维度: ${result.dimension}；这是未保存的候选配置，结果不会写入生效配置，如需重建索引请先点「保存」再测试`);
        } else {
          createMessage.success(`测试成功，维度: ${result.dimension}`);
        }
      } else {
        createMessage.error(`测试失败: ${result.message}`);
      }
      await loadConfig();
    } catch (e: any) {
      createMessage.error(e.message || '测试失败');
    } finally {
      testing.value = false;
    }
  }

  async function handleReindex() {
    reindexing.value = true;
    try {
      const result = await startReindex();
      reindexJob.value = { status: 'running', started_at: new Date().toISOString(), indexed: 0, failed: 0 };
      // 后端互斥：已有回填在跑时返回 already_running + 在跑任务的 job_id，文案要如实
      if ((result as any).status === 'already_running') {
        createMessage.info('已有索引任务在进行中，正在跟随其进度');
      } else {
        createMessage.success('索引任务已启动');
      }
      pollStatus(result.job_id);
    } catch (e: any) {
      createMessage.error(e.message || '启动失败');
    } finally {
      reindexing.value = false;
    }
  }

  async function pollStatus(jobId: string) {
    const poll = async () => {
      try {
        const status = await getReindexStatus(jobId);
        reindexJob.value = status;
        if (status.status === 'running') {
          setTimeout(poll, 2000);
        } else {
          createMessage.success(`索引完成: 成功 ${status.indexed}, 失败 ${status.failed}`);
        }
      } catch (e) {
        console.error('查询进度失败', e);
      }
    };
    setTimeout(poll, 2000);
  }

  onMounted(loadConfig);
</script>
