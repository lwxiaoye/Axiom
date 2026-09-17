<template>
  <a-modal
    v-model:open="open"
    title="配置 MCP 工具"
    :width="680"
    :confirm-loading="saving"
    ok-text="保存配置"
    cancel-text="取消"
    wrap-class-name="wb-modal"
    :ok-button-props="{ disabled: !config.toolList.length }"
    @ok="save"
  >
    <div class="mcp-toolset">
      <div class="wb-msec">
        <div class="wb-msec-head">
          <div>
            <label>MCP Server 地址 <em>*</em></label>
            <p>解析在服务端执行：校验地址安全性后连接 Server 并列出工具，至少发现一个工具才能保存。</p>
          </div>
        </div>
        <div class="url-row">
          <a-input v-model:value="config.url" size="large" placeholder="https://mcp.example.com/mcp" @press-enter="parse" />
          <a-button type="primary" size="large" :loading="parsing" @click="parse">解析</a-button>
        </div>
      </div>

      <div class="wb-msec">
        <div class="wb-msec-head">
          <div>
            <label>鉴权 Header <i>可选</i></label>
            <p>值仅保存在服务端，连接 Server 时附加，不回传浏览器。</p>
          </div>
        </div>
        <div class="wb-rows">
          <div v-for="(row, index) in config.headers" :key="index" class="header-row">
            <a-input v-model:value="row.key" placeholder="Header 名，如 Authorization" class="header-key" />
            <a-input-password v-model:value="row.value" placeholder="Header 值" class="header-value" />
            <a-button type="text" size="small" class="row-remove" title="删除" @click="config.headers.splice(index, 1)">
              <DeleteOutlined />
            </a-button>
          </div>
          <a-button size="small" type="dashed" block @click="config.headers.push({ key: '', value: '' })">
            <PlusOutlined />
            添加 Header
          </a-button>
        </div>
      </div>

      <div class="wb-msec">
        <div class="wb-msec-head">
          <div>
            <label>
              工具清单
              <i v-if="config.toolList.length">已发现 {{ config.toolList.length }} 个</i>
            </label>
            <p>保存后可在对话 Agent 中挂载，由模型按描述自主调用。</p>
          </div>
        </div>
        <div v-if="config.toolList.length" class="wb-rows tool-list">
          <div v-for="tool in config.toolList" :key="tool.name" class="tool-row">
            <strong>{{ tool.name }}</strong>
            <span>{{ tool.description || '暂无描述' }}</span>
          </div>
        </div>
        <div v-else class="tool-empty">尚未解析到工具 — 填写地址后点击「解析」</div>
      </div>
    </div>
  </a-modal>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue';
import { message } from 'ant-design-vue';
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons-vue';
import {
  discoverMcpTools,
  saveWorkflowAppConfig,
  type AiWorkflowApp,
  type McpDiscoveredTool,
} from '../../workflow/api/workflow.api';

const emit = defineEmits<{ (e: 'success'): void }>();

type McpToolSetConfig = {
  url: string;
  headers: { key: string; value: string }[];
  toolList: McpDiscoveredTool[];
};

const open = ref(false);
const saving = ref(false);
const parsing = ref(false);
const record = ref<Partial<AiWorkflowApp>>({});

const config = reactive<McpToolSetConfig>({ url: '', headers: [], toolList: [] });

function init(app: Partial<AiWorkflowApp>) {
  record.value = app;
  let parsed: any = {};
  try {
    parsed = JSON.parse(app.configJson || '{}');
  } catch {
    parsed = {};
  }
  config.url = parsed.url || '';
  config.headers = Array.isArray(parsed.headers) ? parsed.headers : [];
  config.toolList = Array.isArray(parsed.toolList) ? parsed.toolList : [];
  open.value = true;
}

async function parse() {
  if (!config.url.trim()) {
    message.warning('请填写 MCP Server 地址');
    return;
  }
  parsing.value = true;
  try {
    const headers = Object.fromEntries(
      config.headers.filter((row) => row.key.trim()).map((row) => [row.key.trim(), row.value])
    );
    const result = await discoverMcpTools({ url: config.url.trim(), headers });
    config.toolList = result?.tools || [];
    message.success(`解析成功，发现 ${config.toolList.length} 个工具`);
  } catch (error: any) {
    config.toolList = [];
    message.error(error?.response?.data?.detail || 'MCP 工具解析失败');
  } finally {
    parsing.value = false;
  }
}

async function save() {
  if (!config.toolList.length) {
    message.warning('请先解析出至少一个工具');
    return;
  }
  saving.value = true;
  try {
    await saveWorkflowAppConfig({
      id: record.value.id,
      appInfoId: record.value.appInfoId,
      aiAppType: record.value.aiAppType,
      configJson: JSON.stringify({
        url: config.url.trim(),
        headers: config.headers.filter((row) => row.key.trim()),
        toolList: config.toolList,
      }),
    });
    message.success('MCP 工具配置已保存');
    open.value = false;
    emit('success');
  } catch {
    message.error('保存失败');
  } finally {
    saving.value = false;
  }
}

defineExpose({ init });
</script>

<style scoped lang="less">
.mcp-toolset {
  padding: 6px 0 2px;
}

.url-row {
  display: flex;
  gap: 8px;

  .ant-btn {
    flex-shrink: 0;
  }
}

.header-row {
  display: flex;
  gap: 6px;
  align-items: center;
}

.header-key {
  width: 36%;
}

.header-value {
  flex: 1;
}

.row-remove {
  color: #94a3b8;
  flex-shrink: 0;

  &:hover {
    color: #dc2626;
  }
}

.tool-list {
  max-height: 240px;
  overflow: auto;

  .tool-row {
    display: flex;
    flex-direction: column;
    padding: 8px 10px;
    border: 1px solid #eceef3;
    border-radius: 8px;
    background: #fff;

    strong {
      font-size: 13px;
      color: #111827;
    }

    span {
      font-size: 12px;
      color: #94a3b8;
    }
  }
}

.tool-empty {
  padding: 22px 0;
  border: 1px dashed #e2e8f0;
  border-radius: 12px;
  text-align: center;
  color: #98a0ad;
  font-size: 12px;
}
</style>
