<!-- eslint-disable vue/multi-word-component-names -->
<template>
  <div class="p-4">
    <a-card title="联网搜索配置" :bordered="false">
      <template #extra>
        <a-tag :color="formData.enabled ? 'green' : 'default'">
          {{ formData.enabled ? '已启用' : '已关闭' }}
        </a-tag>
      </template>

      <a-alert
        class="mb-4"
        type="info"
        show-icon
        message="搜索 → 抓取 → 重排。搜索服务独立于回答模型，可按部署选择主备顺序。仅使用私有模型服务的学校可关闭 DeepSeek，使用自建搜索；禁止外网访问时应关闭联网搜索。"
      />

      <a-form :model="formData" :label-col="{ span: 6 }" :wrapper-col="{ span: 14 }">
        <a-form-item label="启用联网搜索">
          <a-switch v-model:checked="formData.enabled" />
        </a-form-item>

        <a-divider orientation="left">搜索段</a-divider>
        <a-form-item :label="formData.deepseekFallbackEnabled && formData.providerMode === 'deepseek_first' ? '备用搜索提供方' : '搜索提供方'">
          <a-select v-model:value="formData.searchProvider" :options="searchProviderOptions" />
        </a-form-item>
        <template v-if="formData.searchProvider === 'searxng'">
          <a-form-item label="SearXNG 地址">
            <a-input v-model:value="formData.searxngUrl" placeholder="例如: http://搜索服务器IP:8085" />
          </a-form-item>
          <a-form-item label="指定引擎">
            <a-input v-model:value="formData.searxngEngines" placeholder="逗号分隔，如 baidu,sogou,360search,quark；留空走实例默认" />
            <div style="color: rgba(0,0,0,.45); font-size: 12px; margin-top: 4px;">
              境内服务器建议指定国内引擎；留空时默认聚合可能命中被墙引擎返回 0 结果。
            </div>
          </a-form-item>
          <a-form-item label="SearXNG API Key">
            <a-input-password v-model:value="formData.searxngApiKey" :placeholder="secretPlaceholder('searxngApiKey', '可选')" />
          </a-form-item>
        </template>
        <a-form-item v-else-if="formData.searchProvider === 'serper'" label="Serper API Key">
          <a-input-password v-model:value="formData.serperApiKey" :placeholder="secretPlaceholder('serperApiKey')" />
        </a-form-item>
        <a-form-item v-else-if="formData.searchProvider === 'tavily'" label="Tavily API Key">
          <a-input-password v-model:value="formData.tavilyApiKey" :placeholder="secretPlaceholder('tavilyApiKey')" />
        </a-form-item>

        <a-divider orientation="left">外部搜索服务</a-divider>
        <a-form-item label="允许 DeepSeek 官方搜索">
          <a-switch v-model:checked="formData.deepseekFallbackEnabled" />
          <span class="ml-2 text-gray-500 text-xs">查询会发送到官方服务并产生模型用量；关闭后不调用该服务</span>
        </a-form-item>
        <template v-if="formData.deepseekFallbackEnabled">
          <a-form-item label="搜索顺序">
            <a-select v-model:value="formData.providerMode" :options="searchOrderOptions" />
          </a-form-item>
          <a-form-item label="每次研究预算">
            <a-input-number v-model:value="formData.deepseekResearchMaxQueries" :min="1" :max="12" addon-after="次查询" />
            <span class="ml-2 text-gray-500 text-xs">全队共享 DeepSeek 调用次数，失败也计入；用完后使用备用搜索</span>
          </a-form-item>
          <a-form-item label="凭据与计费">
            <a-space direction="vertical" style="width: 100%">
              <a-space>
                <a-tag color="blue">Responses 原生搜索</a-tag>
                <a-tag>用户专属模型 Key 计费</a-tag>
              </a-space>
              <span class="text-gray-500 text-xs">
                通过平台 NewAPI 的 DeepSeek Responses 服务端搜索，费用归属发起用户；无需另行填写 Key。
              </span>
            </a-space>
          </a-form-item>
          <a-form-item label="搜索模型">
            <a-input v-model:value="formData.deepseekModel" placeholder="deepseek-v4-flash" />
          </a-form-item>
          <a-form-item label="搜索限制">
            <a-input-number v-model:value="formData.deepseekMaxTokens" :min="1024" :max="32768" addon-before="max output tokens" />
            <span class="ml-2 text-gray-500 text-xs">调用次数由本次研究预算统一限制</span>
          </a-form-item>
        </template>

        <a-divider orientation="left">抓取段</a-divider>
        <a-form-item label="正文抓取">
          <a-select v-model:value="formData.scraperProvider" :options="scraperProviderOptions" />
        </a-form-item>
        <template v-if="formData.scraperProvider === 'firecrawl'">
          <a-form-item label="Firecrawl 地址">
            <a-input v-model:value="formData.firecrawlUrl" placeholder="留空使用官方地址" />
          </a-form-item>
          <a-form-item label="Firecrawl API Key">
            <a-input-password v-model:value="formData.firecrawlApiKey" :placeholder="secretPlaceholder('firecrawlApiKey')" />
          </a-form-item>
        </template>
        <a-form-item v-else-if="formData.scraperProvider === 'tavily'" label="Tavily API Key">
          <a-input-password v-model:value="formData.tavilyApiKey" :placeholder="secretPlaceholder('tavilyApiKey')" />
        </a-form-item>

        <a-divider orientation="left">重排段</a-divider>
        <a-form-item label="结果重排">
          <a-select v-model:value="formData.rerankerProvider" :options="rerankerProviderOptions" />
        </a-form-item>
        <a-form-item v-if="formData.rerankerProvider === 'jina'" label="Jina API Key">
          <a-input-password v-model:value="formData.jinaApiKey" :placeholder="secretPlaceholder('jinaApiKey')" />
        </a-form-item>
        <a-form-item v-else-if="formData.rerankerProvider === 'cohere'" label="Cohere API Key">
          <a-input-password v-model:value="formData.cohereApiKey" :placeholder="secretPlaceholder('cohereApiKey')" />
        </a-form-item>
        <a-form-item v-else-if="formData.rerankerProvider === 'local'" label="自托管重排地址">
          <a-input v-model:value="formData.localRerankerUrl" placeholder="例如: http://服务器IP:8082（TEI 兼容 /rerank）" />
        </a-form-item>

        <a-divider orientation="left">通用</a-divider>
        <a-form-item label="返回条数 topK">
          <a-input-number v-model:value="formData.topK" :min="1" :max="20" />
        </a-form-item>

        <a-form-item label="测试结果" v-if="testResult">
          <a-tag :color="testColor">{{ testResult.status }}</a-tag>
          <span class="ml-2 text-gray-500 text-xs">{{ testResult.message }}</span>
        </a-form-item>

        <a-form-item :wrapper-col="{ offset: 6, span: 14 }">
          <a-space>
            <a-button type="primary" :loading="saving" @click="handleSave">保存配置</a-button>
            <a-button v-if="formData.deepseekFallbackEnabled" :loading="testing" @click="handleTest('deepseek-official')">测试 DeepSeek</a-button>
            <a-button :loading="testing" @click="handleTest(formData.searchProvider)">测试自建或第三方搜索</a-button>
          </a-space>
        </a-form-item>
      </a-form>
    </a-card>
  </div>
</template>

<script lang="ts" name="WebSearchConfig" setup>
  import { ref, computed, onMounted } from 'vue';
  import { useMessage } from '/@/hooks/web/useMessage';
  import { getWebSearchConfig, saveWebSearchConfig, testWebSearchConfig } from './websearch.api';

  const { createMessage } = useMessage();
  const saving = ref(false);
  const testing = ref(false);
  const secretsSet = ref<Record<string, boolean>>({});
  const testResult = ref<any>(null);

  const formData = ref({
    enabled: false,
    searchProvider: 'searxng',
    providerMode: 'primary_fallback',
    providerPool: [] as Array<{ id: string; enabled: boolean; weight: number; fallbackOnly?: boolean }>,
    deepseekFallbackEnabled: false,
    deepseekCredentialMode: 'assigned-newapi-key',
    deepseekSearchProtocol: 'responses',
    deepseekModel: 'deepseek-v4-flash',
    deepseekMaxTokens: 4096,
    deepseekResearchHybrid: false,
    deepseekResearchMaxQueries: 3,
    searxngUrl: '',
    searxngEngines: '',
    searxngApiKey: '',
    serperApiKey: '',
    tavilyApiKey: '',
    scraperProvider: 'none',
    firecrawlUrl: '',
    firecrawlApiKey: '',
    rerankerProvider: 'none',
    jinaApiKey: '',
    cohereApiKey: '',
    localRerankerUrl: '',
    topK: 5,
  });

  const searchProviderOptions = [
    { label: 'SearXNG（自托管）', value: 'searxng' },
    { label: 'Serper', value: 'serper' },
    { label: 'Tavily', value: 'tavily' },
  ];
  const searchOrderOptions = [
    { label: 'DeepSeek 优先，上方搜索服务兜底', value: 'deepseek_first' },
    { label: '上方搜索服务优先，DeepSeek 兜底', value: 'primary_fallback' },
  ];
  const scraperProviderOptions = [
    { label: '不抓取正文', value: 'none' },
    { label: 'Firecrawl', value: 'firecrawl' },
    { label: 'Tavily', value: 'tavily' },
  ];
  const rerankerProviderOptions = [
    { label: '不重排', value: 'none' },
    { label: 'Jina', value: 'jina' },
    { label: 'Cohere', value: 'cohere' },
    { label: '自托管（TEI，推荐）', value: 'local' },
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
      const data = await getWebSearchConfig();
      secretsSet.value = data.secrets_set || {};
      Object.keys(formData.value).forEach((key) => {
        if (key in data && !(key in secretsSet.value)) {
          (formData.value as any)[key] = data[key];
        }
      });
      formData.value.deepseekFallbackEnabled = Array.isArray(data.providerPool) && data.providerPool.some(
        (row) => row?.id === 'deepseek-official' && row?.enabled,
      );
    } catch (e) {
      console.error('加载联网搜索配置失败', e);
    }
  }

  async function handleSave() {
    saving.value = true;
    try {
      const providerPool = [
        {
          id: 'deepseek-official', enabled: formData.value.deepseekFallbackEnabled,
          weight: 100, fallbackOnly: formData.value.providerMode !== 'deepseek_first',
        },
        { id: formData.value.searchProvider, enabled: true, weight: 100, fallbackOnly: formData.value.deepseekFallbackEnabled && formData.value.providerMode === 'deepseek_first' },
      ];
      const data = await saveWebSearchConfig({
        ...formData.value,
        providerMode: formData.value.deepseekFallbackEnabled ? formData.value.providerMode : 'primary_fallback',
        deepseekResearchHybrid: false,
        providerPool,
      });
      secretsSet.value = data.secrets_set || {};
      clearSecretInputs();
      createMessage.success('保存成功');
    } catch (e: any) {
      createMessage.error(e.message || '保存失败');
    } finally {
      saving.value = false;
    }
  }

  async function handleTest(provider: string) {
    testing.value = true;
    testResult.value = null;
    try {
      testResult.value = await testWebSearchConfig({ ...formData.value, searchProvider: provider });
    } catch (e: any) {
      testResult.value = { status: 'failed', message: e.message || '测试失败' };
    } finally {
      testing.value = false;
    }
  }

  function clearSecretInputs() {
    ['searxngApiKey', 'serperApiKey', 'tavilyApiKey', 'firecrawlApiKey', 'jinaApiKey', 'cohereApiKey'].forEach(
      (k) => ((formData.value as any)[k] = ''),
    );
  }

  onMounted(loadConfig);
</script>
