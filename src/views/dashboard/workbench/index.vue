<!-- eslint-disable vue/multi-word-component-names -->
<template>
  <div ref="workbenchRef" class="workbench">
    <header class="welcome-row">
      <div>
        <p class="eyebrow">AI MIDDLE PLATFORM</p>
        <h1>{{ displayName }}，您好</h1>
        <span>聚焦今天需要关注的 AI 资产与运行状态</span>
      </div>
      <div class="header-actions">
        <button class="fullscreen-btn" :aria-label="isFullscreen ? '退出全屏' : '全屏'"
          :title="isFullscreen ? '退出全屏' : '全屏'" @click="toggleFullscreen">
          <Icon :icon="isFullscreen ? 'ant-design:fullscreen-exit-outlined' : 'ant-design:fullscreen-outlined'" />
        </button>
        <span class="health-badge"><i></i>平台运行正常</span>
        <div class="period-tabs">
          <button
            v-for="item in periods"
            :key="item.key"
            :class="{ active: period === item.key }"
            @click="period = item.key"
          >{{ item.label }}</button>
        </div>
      </div>
    </header>
    <section class="metric-grid">
      <button v-for="item in metrics" :key="item.label" class="surface metric-card" @click="go(item.path)">
        <i class="metric-icon">
          <Icon :icon="item.icon" />
        </i>
        <span><em>{{ item.label }}</em><strong>{{ item.value }}</strong><small>较昨日 ↑ <b>{{ item.growth
            }}</b></small></span>
        <Icon class="metric-arrow" icon="ant-design:arrow-right-outlined" />
      </button>
    </section>
    <section class="surface quick-bar">
      <div class="section-heading">
        <div>
          <p>快捷操作</p><span>高频业务入口</span>
        </div>
      </div>
      <div class="quick-actions">
        <button v-for="item in quickActions" :key="item.label" @click="go(item.path)"><i>
            <Icon :icon="item.icon" />
          </i><span>{{ item.label }}</span></button>
      </div>
    </section>
    <main class="main-grid">
      <section class="surface capability-panel">
        <div class="section-heading with-action">
          <div>
            <p>AI 能力拓扑</p><span>以应用为出口，统一组织模型、知识与工具能力</span>
          </div>
          <button @click="go('/flow/app/AppInfoList')">查看应用
            <Icon icon="ant-design:right-outlined" />
          </button>
        </div>
        <div class="capability-content">
          <article class="platform-core">
            <i>
              <Icon icon="ant-design:cluster-outlined" />
            </i>
            <div><span>能力中枢</span><strong>AXIOM 校园智能体</strong><small>统一编排 · 权限治理 · 调用观测</small></div>
            <b><i></i>服务中</b>
          </article>
          <div class="capability-grid">
            <button v-for="item in capabilities" :key="item.name" @click="go(item.path)">
              <i :style="{ color: item.color, background: item.bg }">
                <Icon :icon="item.icon" />
              </i>
              <span><strong>{{ item.name }}</strong><small>{{ item.description }}</small></span>
              <em>{{ item.value}}</em>
            </button>
          </div>
        </div>
        <!-- <footer class="capability-footer">
          <span v-for="item in flows" :key="item.label"><i :style="{ background: item.color }"></i>{{ item.label }}<b>{{
              item.value }}</b></span>
        </footer> -->
      </section>
      <aside class="insight-column">
        <section class="surface performance-panel">
          <div class="section-heading">
            <div>
              <p>CPU 使用情况</p><span>性能监控实时数据</span>
            </div>
          </div>
          <div class="performance-body">
            <div class="performance-gauge cpu-gauge" :style="{ background: cpuBackground }">
              <strong>{{ formatPercent(performance.systemCpuUsage) }}</strong><span>系统 CPU</span>
            </div>
            <div class="performance-stats">
              <span><small>当前应用</small><strong>{{ formatPercent(performance.processCpuUsage) }}</strong></span>
              <span><small>CPU 核数</small><strong>{{ performance.cpuCount }} 核</strong></span>
            </div>
          </div>
        </section>
        <section class="surface performance-panel">
          <div class="section-heading">
            <div>
              <p>内存使用情况</p><span>性能监控实时数据</span>
            </div>
          </div>
          <div class="performance-body">
            <div class="performance-gauge memory-gauge" :style="{ background: memoryBackground }">
              <strong>{{ formatPercent(performance.physicalMemoryUsage) }}</strong><span>物理内存</span>
            </div>
            <div class="performance-stats">
              <span><small>物理内存</small><strong>{{ formatMemory(performance.physicalMemoryUsed) }} / {{ formatMemory(performance.physicalMemoryTotal) }}</strong></span>
              <span><small>JVM 内存</small><strong>{{ formatMemory(performance.jvmMemoryUsed) }} / {{ formatMemory(performance.jvmMemoryMax) }}</strong></span>
            </div>
          </div>
        </section>
      </aside>
    </main>
    <section class="detail-grid">
      <!-- <article class="surface detail-card">
        <PanelTitle title="运行任务" hint="知识库与文档处理进度" />
        <div class="task-list"><div v-for="item in tasks" :key="item.name"><span><i></i><strong>{{ item.name }}</strong><small>{{ item.state }}</small></span><em>{{ item.progress }}%</em><u><i :style="{ width: item.progress + '%' }"></i></u></div></div>
      </article>
      <article class="surface detail-card">
        <PanelTitle title="最近导入" hint="最新知识资产" />
        <div class="file-list"><button v-for="item in files" :key="item.name" @click="go('/knowledge/base')"><i><Icon icon="ant-design:file-text-outlined" /></i><span><strong>{{ item.name }}</strong><small>{{ item.chunks }} 分块</small></span><time>{{ item.time }}</time></button></div>
      </article> -->
      <!-- <article class="surface detail-card">
        <PanelTitle title="核心服务" hint="依赖服务可用性" />
        <div class="infra-list">
          <div v-for="item in infrastructure" :key="item.name">
            <Icon :icon="item.icon" /><span><strong>{{ item.name }}</strong><small><i></i>正常
                </small></span><em>{{ item.latency }}</em>
          </div>
        </div>
      </article> -->
    </section>
  </div>
</template>
<script lang="ts" setup>
import { computed, defineComponent, h, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { Icon } from '/@/components/Icon';
import { useUserStore } from '/@/store/modules/user';
import { getServerInfo } from '/@/views/monitor/server/server.api';
import { getWorkbenchOverview } from './workbench.api';
const router = useRouter();
const userStore = useUserStore();
const displayName = computed(() => userStore.getUserInfo?.realname || userStore.getUserInfo?.username || '管理员');
const periods = [{ key: 'today', label: '今日' }, { key: '7d', label: '近7天' }, { key: '30d', label: '近30天' }];
const period = ref('today');
const isFullscreen = ref(false);
const workbenchRef = ref<HTMLElement | null>(null);

const PanelTitle = defineComponent({
  props: {
    title: { type: String, required: true },
    hint: { type: String, required: true }
  },
  setup(props) {
    return () => h('div', { class: 'panel-title' }, [
      h('div', [h('h2', props.title), h('span', props.hint)]),
      h(Icon, { icon: 'ant-design:ellipsis-outlined' })
    ]);
  }
});
// 指标卡片
const metrics = ref([
  ['用户令牌', 42, '8.2%', 'ant-design:robot-outlined', '/token'],
  ['应用', 18, '5.6%', 'ant-design:appstore-outlined', '/flow/app/AppInfoList'],
  ['知识库', 12, '9.1%', 'ant-design:book-outlined', '/knowledge/base'],
  ['Skill', 67, '7.3%', 'ant-design:thunderbolt-outlined', '/skills/list']
].map(([label, value, growth, icon, path]) => ({ label, value, growth, icon, path })));
// 快捷操作
const quickActions = [
  ['新建应用', 'ant-design:appstore-add-outlined', '/flow/app/AppInfoList'],
  ['新建智能体', 'ant-design:robot-outlined', '/center/my-agent'],
  ['上传知识', 'ant-design:cloud-upload-outlined', '/knowledge/base'],
  ['新建 Skill', 'ant-design:tag-outlined', '/skills/list'],
  ['请求追踪', 'ant-design:code-outlined', '/monitor/trace'],
  ['查看日志', 'ant-design:file-search-outlined', '/monitor/log']
].map(([label, icon, path]) => ({ label, icon, path }));
// 能力模块
const capabilities = ref([
  ['用户令牌', '统一管理访问令牌', '0', 'ant-design:key-outlined', '#2778ff', '#edf5ff', '/token'],
  ['智能体', '任务编排与执行', '0', 'ant-design:robot-outlined', '#7564e8', '#f2f0ff', '/peopleCenter/index'],
  ['知识库', '文档解析与混合检索', '0', 'ant-design:book-outlined', '#00a687', '#eafaf6', '/knowledge/base'],
  ['Skill 工具', '标准化能力复用', '0', 'ant-design:thunderbolt-outlined', '#f18a2a', '#fff5e9', '/skills/SkillList'],
  ['业务应用', '场景化 AI 能力落地', '0', 'ant-design:appstore-outlined', '#1688ff', '#edf7ff', '/flow/app/AppInfoList'],
  ['API 开放', '统一鉴权与调用', '0', 'ant-design:api-outlined', '#db5f89', '#fff0f5', '/openapi/OpenApiList']
].map(([name, description, value, icon, color, bg, path]) => ({ name, description, value, icon, color, bg, path })));
// 底部统计
const flows = ref([
  { label: '今日调用', value: '23.8K', color: '#1688ff' },
  { label: '索引分块', value: '8.4M', color: '#00a687' },
  { label: '执行成功率', value: '99.2%', color: '#7564e8' }
]);
const performance = ref({
  cpuCount: 0,
  systemCpuUsage: 0,
  processCpuUsage: 0,
  physicalMemoryUsage: 0,
  physicalMemoryUsed: 0,
  physicalMemoryTotal: 0,
  jvmMemoryUsed: 0,
  jvmMemoryMax: 0,
});
const cpuBackground = computed(() => progressBackground(performance.value.systemCpuUsage, '#1677ff', '#dcecff'));
const memoryBackground = computed(() => progressBackground(performance.value.physicalMemoryUsage, '#7564e8', '#e6e1ff'));
function formatNumber(value: number) { return value >= 1000 ? `${(value / 1000).toFixed(1)}K` : String(value); }
function safeNumber(value: unknown) {
  const numberValue = Number(value);
  return Number.isFinite(numberValue) ? numberValue : 0;
}
function clampPercent(value: number) { return Math.min(100, Math.max(0, value)); }
function formatPercent(value: number) { return `${clampPercent(value).toFixed(1)}%`; }
function formatMemory(bytes: number) {
  const megabytes = bytes / 1048576;
  return megabytes >= 1024 ? `${(megabytes / 1024).toFixed(1)} GB` : `${Math.round(megabytes)} MB`;
}
function progressBackground(value: number, color: string, restColor: string) {
  const percentage = clampPercent(value);
  return `radial-gradient(circle, #fff 57%, transparent 59%), conic-gradient(${color} 0 ${percentage}%, ${restColor} ${percentage}%)`;
}
function metricValue(metrics: Array<{ name?: string; measurements?: Array<{ value?: number }> }>, name: string) {
  return safeNumber(metrics.find((item) => item.name === name)?.measurements?.[0]?.value);
}
function memoryValue(metrics: Array<{ result?: Record<string, number> }>, name: string) {
  return safeNumber(metrics[0]?.result?.[name]);
}
async function loadOverview() {
  const result = await getWorkbenchOverview(period.value).catch(() => undefined);
  if (!result) return;
  const metricKeys = ['tokens', 'applications', 'knowledgeBases', 'skills'];
  metrics.value.forEach((item, index) => { const value = result.metrics[metricKeys[index]]; item.value = value?.value ?? 0; item.growth = `${value?.growth ?? 0}%`; });
  const capabilityKeys = ['tokens', 'agents', 'knowledgeBases', 'skills', 'applications', 'openApis'];
  capabilities.value.forEach((item, index) => item.value = result.capabilities[capabilityKeys[index]] ?? 0);
  capabilities.value[0].value = result.metrics.tokens?.value ?? 0;
  flows.value = [{ label: '今日调用', value: formatNumber(result.usage.today), color: '#1688ff' }, { label: '索引分块', value: formatNumber(result.knowledge.chunks), color: '#00a687' }, { label: '执行成功率', value: `${result.knowledge.successRate}%`, color: '#7564e8' }];
}
async function loadPerformance() {
  const [cpuMetrics, memoryMetrics] = await Promise.all([
    getServerInfo('1'),
    getServerInfo('5'),
  ]).catch(() => [[], []]);
  performance.value = {
    cpuCount: Math.round(metricValue(cpuMetrics, 'system.cpu.count')),
    systemCpuUsage: clampPercent(metricValue(cpuMetrics, 'system.cpu.usage') * 100),
    processCpuUsage: clampPercent(metricValue(cpuMetrics, 'process.cpu.usage') * 100),
    physicalMemoryUsage: clampPercent(memoryValue(memoryMetrics, 'memory.physical.usage') * 100),
    physicalMemoryUsed: memoryValue(memoryMetrics, 'memory.physical.used'),
    physicalMemoryTotal: memoryValue(memoryMetrics, 'memory.physical.total'),
    jvmMemoryUsed: memoryValue(memoryMetrics, 'memory.runtime.used'),
    jvmMemoryMax: memoryValue(memoryMetrics, 'memory.runtime.max'),
  };
}
// 任务列表
const tasks = [
  ['产品手册_v2.pdf', '索引中', 68],
  ['合同模板.docx', '解析中', 42],
  ['技术文档库', '等待重建', 12]
].map(([name, state, progress]) => ({ name, state, progress }));
// 知识库文件
const files = [
  ['产品手册_v2.pdf', '2,450', '10:20'],
  ['技术白皮书_2024.pdf', '3,820', '昨天'],
  ['API 接口文档', '1,258', '06-18']
].map(([name, chunks, time]) => ({ name, chunks, time }));
// 底层基础设施
const infrastructure = [
  ['APISIX', 'ant-design:gateway-outlined', '18ms'],
  ['RocketMQ', 'ant-design:deployment-unit-outlined', '12ms'],
  ['Qdrant', 'ant-design:search-outlined', '22ms'],
  ['MySQL', 'ant-design:database-outlined', '8ms'],
  ['MinIO', 'ant-design:cloud-server-outlined', '15ms']
].map(([name, icon, latency]) => ({ name, icon, latency }));
function go(path: string) {
  router.push(path);
}
function syncFullscreen() {
  isFullscreen.value = document.fullscreenElement === workbenchRef.value;
  window.setTimeout(() => window.dispatchEvent(new Event('resize')), 80);
}
async function toggleFullscreen() {
  if (document.fullscreenElement === workbenchRef.value) {
    await document.exitFullscreen();
  } else if (workbenchRef.value) {
    await workbenchRef.value.requestFullscreen();
  }
  syncFullscreen();
}
onMounted(() => {
  document.addEventListener('fullscreenchange', syncFullscreen);
  loadOverview();
  loadPerformance();
});
watch(period, loadOverview);
onBeforeUnmount(() => document.removeEventListener('fullscreenchange', syncFullscreen));
</script>
<style lang="less" scoped>
.workbench {
  --primary: #1677ff;
  --text: #172033;
  --muted: #748094;
  --line: #e5ebf2;
  --surface: #fff;
  min-height: calc(100vh - 96px);
  padding: 28px 32px 40px;
  color: var(--text);
  background: #f7f9fc;
  overflow-x: hidden
}
.workbench:fullscreen {
  height: 100vh;
  min-height: 100vh;
  overflow: auto;
  padding: 28px 32px 40px
}
.surface {
  border: 1px solid var(--line);
  border-radius: 14px;
  background: var(--surface);
  box-shadow: 0 8px 28px rgba(31, 55, 90, .045)
}
button {
  font: inherit
}
.welcome-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 24px
}
.eyebrow {
  margin: 0 0 6px;
  color: var(--primary);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 2px
}
.welcome-row h1 {
  margin: 0 0 8px;
  font-size: 30px;
  line-height: 1.2
}
.welcome-row>div>span {
  color: var(--muted);
  font-size: 14px
}
.header-actions {
  display: flex;
  align-items: center;
  gap: 12px
}
.fullscreen-btn {
  display: grid;
  place-items: center;
  width: 38px;
  height: 38px;
  color: #526176;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: #fff;
  font-size: 18px;
  cursor: pointer;
  transition: .2s
}
.fullscreen-btn:hover {
  color: var(--primary);
  border-color: #b7d7ff;
  background: #f3f8ff
}
.health-badge {
  display: flex;
  align-items: center;
  gap: 8px !important;
  padding: 9px 13px;
  color: #168452 !important;
  border: 1px solid #d8f0e4;
  border-radius: 10px;
  background: #f3fbf7;
  font-size: 12px !important
}
.health-badge i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #18b76a
}
.period-tabs {
  display: flex;
  padding: 4px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: #fff
}
.period-tabs button {
  min-width: 66px;
  height: 34px;
  color: #68758a;
  border: 0;
  border-radius: 7px;
  background: transparent;
  cursor: pointer
}
.period-tabs button.active {
  color: #fff;
  background: var(--primary);
  box-shadow: 0 4px 12px rgba(22, 119, 255, .2)
}
.metric-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 18px;
  margin-bottom: 20px
}
.metric-card {
  position: relative;
  display: grid;
  grid-template-columns: 52px 1fr 24px;
  align-items: center;
  gap: 14px;
  min-height: 116px;
  padding: 20px 22px;
  text-align: left;
  cursor: pointer;
  transition: border-color .2s, box-shadow .2s
}
.metric-card:hover {
  border-color: #b7d7ff;
  box-shadow: 0 12px 30px rgba(22, 119, 255, .09)
}
.metric-icon {
  display: grid;
  place-items: center;
  width: 52px;
  height: 52px;
  color: var(--primary);
  border-radius: 13px;
  background: #edf5ff;
  font-size: 25px
}
.metric-card span {
  display: flex;
  flex-direction: column
}
.metric-card em {
  color: #66758a;
  font-style: normal
}
.metric-card strong {
  margin: 4px 0;
  font-size: 28px;
  line-height: 1
}
.metric-card small {
  color: #8994a5;
  font-size: 11px
}
.metric-card b {
  color: #17a668
}
.metric-arrow {
  color: #b7c1cf
}
.quick-bar {
  display: grid;
  grid-template-columns: 170px minmax(0, 1fr);
  align-items: center;
  gap: 24px;
  margin-bottom: 20px;
  padding: 18px 22px
}
.section-heading p,
.panel-title h2 {
  margin: 0;
  color: #1b2638;
  font-size: 16px;
  font-weight: 700
}
.section-heading span,
.panel-title span {
  display: block;
  margin-top: 5px;
  color: #8793a5;
  font-size: 12px
}
.quick-actions {
  display: grid;
  grid-template-columns: repeat(6, minmax(112px, 1fr));
  gap: 10px;
  min-width: 0
}
.quick-actions button {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 54px;
  padding: 8px 12px;
  color: #455268;
  border: 1px solid #edf1f5;
  border-radius: 10px;
  background: #fafcff;
  cursor: pointer;
  white-space: nowrap
}
.quick-actions button>span {
  white-space: nowrap
}
.quick-actions button:hover {
  color: var(--primary);
  border-color: #cde2ff;
  background: #f3f8ff
}
.quick-actions i {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  color: var(--primary);
  border-radius: 8px;
  background: #eaf4ff;
  font-size: 18px
}
.main-grid {
  display: grid;
  grid-template-columns: minmax(620px, 1.7fr) minmax(360px, .8fr);
  align-items: stretch;
  gap: 20px;
  margin-bottom: 20px
}
.capability-panel {
  display: flex;
  flex-direction: column;
  min-height: 500px;
  padding: 24px
}
.with-action {
  display: flex;
  align-items: center;
  justify-content: space-between
}
.with-action button {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 8px 0;
  color: var(--primary);
  border: 0;
  background: none;
  cursor: pointer
}
.capability-content {
  display: grid;
  grid-template-columns: 220px 1fr;
  flex: 1;
  gap: 22px;
  margin-top: 24px
}
.platform-core {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  min-height: 0;
  padding: 24px;
  color: #fff;
  border-radius: 16px;
  background: linear-gradient(145deg, #1272ec, #0b55b3);
  box-shadow: 0 16px 34px rgba(19, 107, 218, .22)
}
.platform-core>i {
  display: grid;
  place-items: center;
  width: 52px;
  height: 52px;
  border: 1px solid rgba(255, 255, 255, .22);
  border-radius: 13px;
  background: rgba(255, 255, 255, .12);
  font-size: 27px
}
.platform-core div {
  display: flex;
  flex-direction: column
}
.platform-core div span {
  color: #cfe4ff;
  font-size: 12px
}
.platform-core div strong {
  margin: 8px 0;
  font-size: 24px
}
.platform-core div small {
  color: #dbeaff;
  line-height: 1.7
}
.platform-core>b {
  display: flex;
  align-items: center;
  gap: 7px;
  width: max-content;
  padding: 7px 10px;
  border-radius: 8px;
  background: rgba(255, 255, 255, .12);
  font-size: 11px
}
.platform-core>b i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #7cffc1
}
.capability-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  grid-auto-rows: 1fr;
  gap: 12px
}
.capability-grid button {
  display: grid;
  grid-template-columns: 44px 1fr 30px;
  align-items: center;
  gap: 12px;
  padding: 16px;
  text-align: left;
  border: 1px solid #e8edf3;
  border-radius: 12px;
  background: #fff;
  cursor: pointer
}
.capability-grid button:hover {
  border-color: #b9d7ff;
  background: #fbfdff
}
.capability-grid button>i {
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  border-radius: 11px;
  font-size: 21px
}
.capability-grid button>span {
  display: flex;
  flex-direction: column;
  min-width: 0
}
.capability-grid strong {
  font-size: 13px
}
.capability-grid small {
  margin-top: 5px;
  color: #8994a5;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis
}
.capability-grid em {
  color: #334155;
  font-size: 20px;
  font-style: normal;
  font-weight: 700;
  text-align: right
}
.capability-footer {
  display: flex;
  gap: 30px;
  margin-top: 20px;
  padding: 15px 18px;
  border-radius: 10px;
  background: #f7f9fc
}
.capability-footer span {
  display: flex;
  align-items: center;
  gap: 7px;
  color: #758195;
  font-size: 12px
}
.capability-footer span>i {
  width: 7px;
  height: 7px;
  border-radius: 50%
}
.capability-footer b {
  color: #263247
}
.insight-column {
  display: grid;
  grid-template-rows: repeat(2, minmax(220px, 1fr));
  gap: 20px
}
.performance-panel {
  min-width: 0;
  min-height: 220px;
  padding: 22px;
  overflow: hidden
}
.performance-body {
  display: grid;
  grid-template-columns: 130px minmax(0, 1fr);
  align-items: center;
  gap: 10px;
  margin-top: 12px
}
.performance-gauge {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  width: 112px;
  height: 112px;
  border-radius: 50%;
}
.performance-gauge strong {
  font-size: 19px
}
.performance-gauge span {
  margin-top: 3px;
  color: #8390a2;
  font-size: 10px
}
.performance-stats {
  display: grid;
  gap: 12px
}
.performance-stats span {
  padding: 9px 12px;
  border-left: 2px solid #d9e9ff
}
.memory-gauge + .performance-stats span {
  border-left-color: #e6e1ff
}
.performance-stats small {
  display: block;
  color: #8793a5
}
.performance-stats strong {
  display: block;
  margin-top: 3px;
  font-size: 15px
}
.detail-grid {
  display: grid;
  // grid-template-columns: 1.1fr 1.1fr .9fr;
  gap: 20px
}
.detail-card {
  min-height: 230px;
  padding: 22px
}
.panel-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 18px
}
.panel-title>:deep(svg) {
  color: #9aa5b5
}
.task-list>div {
  display: grid;
  grid-template-columns: 1fr 40px;
  align-items: center;
  margin-top: 15px
}
.task-list span {
  display: grid;
  grid-template-columns: 10px 1fr auto;
  align-items: center;
  gap: 6px
}
.task-list span>i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--primary)
}
.task-list span strong {
  font-size: 12px;
  font-weight: 500
}
.task-list span small {
  color: #8591a3
}
.task-list em {
  color: #64748b;
  font-style: normal;
  text-align: right
}
.task-list u {
  grid-column: 1/3;
  height: 5px;
  margin-top: 8px;
  border-radius: 4px;
  background: #edf1f5;
  text-decoration: none;
  overflow: hidden
}
.task-list u i {
  display: block;
  height: 100%;
  border-radius: 4px;
  background: var(--primary)
}
.file-list button {
  display: grid;
  grid-template-columns: 38px 1fr auto;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-height: 54px;
  padding: 7px 4px;
  text-align: left;
  border: 0;
  border-bottom: 1px solid #edf1f5;
  background: #fff;
  cursor: pointer
}
.file-list button>i {
  display: grid;
  place-items: center;
  width: 34px;
  height: 36px;
  color: var(--primary);
  border-radius: 8px;
  background: #edf5ff;
  font-size: 18px
}
.file-list button>span {
  display: flex;
  flex-direction: column;
  min-width: 0
}
.file-list strong {
  overflow: hidden;
  font-size: 12px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap
}
.file-list small,
.file-list time {
  margin-top: 4px;
  color: #8b96a6;
  font-size: 10px
}
.infra-list {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr 1fr 1fr;
  gap: 10px
}
.infra-list>div {
  display: grid;
  grid-template-columns: 30px 1fr auto;
  align-items: center;
  gap: 8px;
  padding: 12px;
  border: 1px solid #edf1f5;
  border-radius: 10px
}
.infra-list>div>:deep(svg) {
  color: var(--primary);
  font-size: 19px
}
.infra-list span {
  display: flex;
  flex-direction: column
}
.infra-list strong {
  font-size: 11px
}
.infra-list small {
  margin-top: 3px;
  color: #17a668;
  font-size: 9px
}
.infra-list small i {
  display: inline-block;
  width: 5px;
  height: 5px;
  margin-right: 4px;
  border-radius: 50%;
  background: #17b26a
}
.infra-list em {
  color: #68758a;
  font-size: 10px;
  font-style: normal
}
@media(max-width:1300px) {
  .workbench {
    padding: 24px
  }
  .quick-bar {
    grid-template-columns: 1fr
  }
  .main-grid {
    grid-template-columns: 1fr
  }
  .insight-column {
    grid-template-columns: 1fr 1fr;
    grid-template-rows: auto
  }
  .quick-bar {
    align-items: flex-start;
    flex-direction: column
  }
  .quick-actions {
    width: 100%;
    max-width: none
  }
  .detail-grid {
    grid-template-columns: 1fr 1fr
  }
  .detail-card:last-child {
    grid-column: 1/3
  }
}
@media(max-width:850px) {
  .metric-grid {
    grid-template-columns: 1fr 1fr
  }
  .quick-actions {
    grid-template-columns: repeat(3, 1fr)
  }
  .capability-content {
    grid-template-columns: 1fr
  }
  .platform-core {
    min-height: 190px
  }
  .detail-grid {
    grid-template-columns: 1fr
  }
  .detail-card:last-child {
    grid-column: auto
  }
  .welcome-row {
    align-items: flex-start;
    flex-direction: column;
    gap: 16px
  }
}
@media(max-width:560px) {
  .workbench {
    padding: 18px
  }
  .metric-grid,
  .quick-actions,
  .capability-grid,
  .insight-column {
    grid-template-columns: 1fr
  }
  .header-actions {
    align-items: flex-start;
    flex-direction: column
  }
  .performance-body {
    grid-template-columns: 1fr
  }
  .performance-gauge {
    margin: auto
  }
  .infra-list {
    grid-template-columns: 1fr
  }
}
</style>
