<template>
  <main class="admin-page">
    <header class="page-heading">
      <button type="button" class="back" @click="goBack">
        <ArrowLeftOutlined />
        <span>{{ canGoBack ? '返回' : '回到工作台' }}</span>
      </button>
      <div>
        <h1>管理配置</h1>
        <p>仅管理员可见。这里只放平台级设置，个人偏好在各自页面里调整。</p>
      </div>
    </header>

    <nav class="tabs" role="tablist">
      <button
        v-for="t in TABS"
        :key="t.key"
        type="button"
        role="tab"
        class="tab"
        :class="{ active: tab === t.key }"
        :aria-selected="tab === t.key"
        @click="tab = t.key"
      >
        {{ t.label }}
      </button>
    </nav>

    <!-- 对话模型 -->
    <section v-show="tab === 'model'" class="card">
      <div v-if="model.loading" class="muted">正在加载…</div>
      <template v-else>
        <p class="hint">此配置为平台默认，对所有登录用户生效；用户可在工作台「模型配置」页用自己的 API Key 覆盖。</p>
        <label for="m-base">请求地址</label>
        <input id="m-base" v-model="model.form.base_url" type="url" placeholder="https://api.example.com/v1" :disabled="model.busy" />
        <label for="m-key">API Key <span v-if="model.hasKey" class="ok">已配置</span></label>
        <input id="m-key" v-model="model.form.api_key" type="password" autocomplete="new-password"
               :placeholder="model.hasKey ? '留空则保持原密钥' : '输入服务商提供的 API Key'" :disabled="model.busy" />
        <label for="m-name">模型名称</label>
        <input id="m-name" v-model="model.form.model" placeholder="例如 gpt-4o-mini" :disabled="model.busy" />
        <div class="switch-row">
          <div><strong>启用此配置</strong><p>关闭后回退到原有模型网关。</p></div>
          <a-switch v-model:checked="model.form.enabled" :disabled="model.busy" aria-label="启用对话模型配置" />
        </div>
        <Feedback :state="model.feedback" />
        <div class="actions">
          <button type="button" class="secondary" :disabled="model.busy" @click="testModel">
            {{ model.testing ? '测试中…' : '测试连接' }}
          </button>
          <button type="button" class="primary" :disabled="model.busy" @click="saveModel">
            {{ model.saving ? '保存中…' : '保存' }}
          </button>
        </div>
      </template>
    </section>

    <!-- 向量模型 -->
    <section v-show="tab === 'embedding'" class="card">
      <div v-if="emb.loading" class="muted">正在加载…</div>
      <template v-else>
        <p class="hint">知识库检索依赖向量模型。更换模型或维度后需要重建索引。</p>
        <label for="e-base">请求地址</label>
        <input id="e-base" v-model="emb.form.base_url" type="url" placeholder="https://api.example.com/v1" :disabled="emb.busy" />
        <label for="e-key">API Key <span v-if="emb.masked" class="ok">{{ emb.masked }}</span></label>
        <input id="e-key" v-model="emb.form.api_key" type="password" autocomplete="new-password"
               :placeholder="emb.masked ? '留空则保持原密钥' : '输入 API Key'" :disabled="emb.busy" />
        <label for="e-model">模型名称</label>
        <input id="e-model" v-model="emb.form.model" placeholder="例如 text-embedding-3-small" :disabled="emb.busy" />
        <p v-if="emb.dimension" class="hint">当前维度：{{ emb.dimension }}</p>
        <Feedback :state="emb.feedback" />
        <div class="actions">
          <button type="button" class="secondary" :disabled="emb.busy" @click="testEmbedding">
            {{ emb.testing ? '测试中…' : '测试连接' }}
          </button>
          <button type="button" class="primary" :disabled="emb.busy" @click="saveEmbedding">
            {{ emb.saving ? '保存中…' : '保存' }}
          </button>
        </div>
      </template>
    </section>

    <!-- 重排模型 -->
    <section v-show="tab === 'embedding'" class="card">
      <div v-if="rerank.loading" class="muted">正在加载…</div>
      <template v-else>
        <h2>重排模型</h2>
        <p class="hint">用于知识库与联网搜索结果的相关性重排；留空则不重排。</p>
        <label for="r-base">请求地址</label>
        <input id="r-base" v-model="rerank.form.base_url" type="url" placeholder="https://api.example.com/v1" :disabled="rerank.busy" />
        <label for="r-key">API Key <span v-if="rerank.hasKey" class="ok">已配置</span></label>
        <input id="r-key" v-model="rerank.form.api_key" type="password" autocomplete="new-password"
               :placeholder="rerank.hasKey ? '留空则保持原密钥' : '输入 API Key'" :disabled="rerank.busy" />
        <label for="r-model">模型名称</label>
        <input id="r-model" v-model="rerank.form.model" placeholder="例如 qwen3.7-text-rerank" :disabled="rerank.busy" />
        <div class="switch-row">
          <div><strong>启用重排</strong><p>关闭后只按向量相似度排序。</p></div>
          <a-switch v-model:checked="rerank.form.enabled" :disabled="rerank.busy" aria-label="启用重排模型" />
        </div>
        <Feedback :state="rerank.feedback" />
        <div class="actions">
          <button type="button" class="secondary" :disabled="rerank.busy" @click="testRerank">
            {{ rerank.testing ? '测试中…' : '测试连接' }}
          </button>
          <button type="button" class="primary" :disabled="rerank.busy" @click="saveRerank">
            {{ rerank.saving ? '保存中…' : '保存' }}
          </button>
        </div>
      </template>
    </section>

    <!-- 联网搜索 -->
    <section v-show="tab === 'search'" class="card">
      <div v-if="search.loading" class="muted">正在加载…</div>
      <template v-else>
        <div class="switch-row first">
          <div><strong>启用联网搜索</strong><p>关闭后智能体只使用本地知识。</p></div>
          <a-switch v-model:checked="search.form.enabled" :disabled="search.busy" aria-label="启用联网搜索" />
        </div>
        <label for="s-url">SearXNG 地址</label>
        <input id="s-url" v-model="search.form.searxngUrl" type="url" placeholder="http://searxng:8080" :disabled="search.busy" />
        <p class="hint">本机已随 compose 部署 SearXNG，容器内地址通常是 http://searxng:8080。</p>
        <label for="s-engines">搜索引擎</label>
        <input id="s-engines" v-model="search.form.searxngEngines" placeholder="duckduckgo,brave" :disabled="search.busy" />
        <p class="hint">逗号分隔的 SearXNG 引擎名。本机实测 duckduckgo、brave 可用；bing 解析已失效，google/baidu 对本机 IP 出验证码。</p>
        <label for="s-rerank">结果重排</label>
        <select id="s-rerank" v-model="search.form.rerankerProvider" :disabled="search.busy">
          <option value="none">不重排</option>
          <option value="platform">平台重排模型</option>
          <!-- jina/cohere/local 仍由后端支持但不在这页配；库里若是这三者之一，原值照常显示并随保存原样带回，不悄悄改掉 -->
          <option v-if="!SIMPLE_RERANKERS.includes(search.form.rerankerProvider)" :value="search.form.rerankerProvider" disabled>
            {{ search.form.rerankerProvider }}（旧配置）
          </option>
        </select>
        <p class="hint">平台重排模型未配置时按不重排处理。</p>
        <Feedback :state="search.feedback" />
        <div class="actions">
          <button type="button" class="secondary" :disabled="search.busy" @click="testSearch">
            {{ search.testing ? '测试中…' : '测试连接' }}
          </button>
          <button type="button" class="primary" :disabled="search.busy" @click="saveSearch">
            {{ search.saving ? '保存中…' : '保存' }}
          </button>
        </div>
      </template>
    </section>

    <!-- 校园百事通 -->
    <section v-show="tab === 'campus'" class="card">
      <div v-if="campus.loading" class="muted">正在加载…</div>
      <div v-else-if="campus.error" class="feedback error">{{ campus.error }}</div>
      <template v-else>
        <dl class="status">
          <div><dt>状态</dt><dd>{{ campus.enabled ? '已启用' : '未启用' }}</dd></div>
          <div><dt>已发布版本</dt><dd>{{ campus.revision === 0 ? '尚未发布' : campus.revision }}</dd></div>
          <div><dt>草稿状态</dt><dd>{{ campus.draftStatus || '无草稿' }}</dd></div>
        </dl>

        <label for="c-model">对话模型</label>
        <select id="c-model" v-model="campus.modelId" :disabled="campus.busy || !campus.models.length">
          <option value="">{{ campus.models.length ? '请选择模型' : '暂无可用模型' }}</option>
          <option v-for="m in campus.models" :key="modelValue(m)" :value="modelValue(m)">
            {{ modelLabel(m) }}
          </option>
        </select>
        <p v-if="!campus.models.length" class="hint">
          先在「对话模型」里配置并启用一个模型，这里才会出现可选项。
        </p>

        <label for="c-domains">学校官方域名</label>
        <textarea id="c-domains" v-model="campus.domains" rows="4"
                  placeholder="每行一个域名，例如 www.example.edu.cn" :disabled="campus.busy"></textarea>
        <p class="hint">官网检索与配图只允许来自该白名单，用于保证回答「有据可查」。至少填一个。</p>

        <label>绑定知识库</label>
        <div v-if="!campus.knowledgeBases.length" class="hint">
          还没有可绑定的知识库。先到工作台「我的知识库」建库并上传文档，发布要求至少绑定一个可用知识库。
        </div>
        <div v-else class="check-list">
          <label v-for="kb in campus.knowledgeBases" :key="kb.id" class="check-item">
            <input type="checkbox" :value="kb.id" v-model="campus.bindings" :disabled="campus.busy" />
            <span>{{ kb.name }}</span>
            <small>{{ kb.documentCount }} 个文档 · {{ kb.chunkCount }} 个分段{{ kb.status === 'ACTIVE' ? '' : ' · 已停用' }}</small>
          </label>
        </div>
        <p class="hint">回答校园问题时只在勾选的知识库里检索。</p>

        <label for="c-note">变更说明</label>
        <input id="c-note" v-model="campus.note" placeholder="本次修改的简要说明" :disabled="campus.busy" />

        <div v-if="campus.issues.length" class="feedback error" role="alert">
          <strong>发布前需要解决：</strong>
          <ul class="issue-list"><li v-for="(it, i) in campus.issues" :key="i">{{ it }}</li></ul>
        </div>
        <Feedback :state="campus.feedback" />

        <div class="actions">
          <button type="button" class="secondary" :disabled="campus.busy" @click="checkCampus">
            {{ campus.checking ? '检查中…' : '检查配置' }}
          </button>
          <button type="button" class="secondary" :disabled="campus.busy" @click="saveCampusDraft">
            {{ campus.saving ? '保存中…' : '保存草稿' }}
          </button>
          <button type="button" class="primary" :disabled="campus.busy" @click="publishCampus">
            {{ campus.publishing ? '发布中…' : '发布' }}
          </button>
        </div>
      </template>
    </section>

    <!-- 用户 -->
    <section v-show="tab === 'users'" class="card">
      <div v-if="users.loading" class="muted">正在加载…</div>
      <div v-else-if="users.error" class="feedback error">{{ users.error }}</div>
      <template v-else>
        <p class="hint">开放注册已启用，任何人都可以自行创建账号。此处仅供查看。</p>
        <table class="user-table">
          <thead><tr><th>用户名</th><th>姓名</th><th>角色</th></tr></thead>
          <tbody>
            <tr v-for="u in users.list" :key="u.username">
              <td>{{ u.username }}</td>
              <td>{{ u.realname }}</td>
              <td>{{ (u.roles && u.roles[0]?.roleName) || '成员' }}</td>
            </tr>
          </tbody>
        </table>
        <div class="actions"><button type="button" class="secondary" @click="loadUsers">刷新</button></div>
      </template>
    </section>
  </main>
</template>

<script setup lang="ts">
  import { h, onMounted, reactive, ref, watch } from 'vue';
  import { useRouter } from 'vue-router';
  import { ArrowLeftOutlined } from '@ant-design/icons-vue';
  import { requestAgentApi } from '../agentApi';
  import { defHttp } from '/@/utils/http/axios';
  import { usePermission } from '/@/hooks/web/usePermission';
  import { usePageBack } from '/@/hooks/web/usePageBack';

  // 从工作台右上角进来的常态是有上一页可回；直接贴地址栏打开时退回工作台。
  const { goBack, canGoBack } = usePageBack('/center/chat');

  // 后端对这些接口本就要求管理员（403），这里只是让误入的普通用户体面地退出去。
  const router = useRouter();
  const { hasPermission } = usePermission();
  const allowed = hasPermission('admin:manager', false);

  type Result = { success: boolean; message: string; latency_ms?: number };

  /** 统一的成功/失败提示，五个板块共用。 */
  const Feedback = (props: { state: Result | null }) =>
    props.state
      ? h(
          'div',
          { class: ['feedback', props.state.success ? 'success' : 'error'], role: 'status' },
          props.state.message + (props.state.latency_ms != null ? ` · ${props.state.latency_ms} ms` : '')
        )
      : null;

  const TABS = [
    { key: 'model', label: '对话模型' },
    { key: 'embedding', label: '向量模型' },
    { key: 'search', label: '联网搜索' },
    { key: 'campus', label: '校园百事通' },
    { key: 'users', label: '用户' },
  ] as const;
  const tab = ref<(typeof TABS)[number]['key']>('model');

  function fail(e: any, fallback: string): Result {
    return { success: false, message: e?.message || fallback };
  }

  // ---- 对话模型 ----
  const model = reactive({
    loading: true, saving: false, testing: false, busy: false, hasKey: false,
    feedback: null as Result | null,
    form: { base_url: '', model: '', api_key: '', enabled: true },
  });
  watch(() => [model.saving, model.testing], () => { model.busy = model.saving || model.testing; });
  watch(() => ({ ...model.form }), () => { model.feedback = null; }, { deep: true });

  async function loadModel() {
    model.loading = true;
    try {
      const d = await requestAgentApi<any>('/model-connection');
      Object.assign(model.form, { base_url: d.base_url, model: d.model, api_key: '', enabled: d.has_api_key ? d.enabled : true });
      model.hasKey = !!d.has_api_key;
    } catch (e: any) {
      model.feedback = fail(e, '配置加载失败');
    } finally {
      model.loading = false;
    }
  }
  async function saveModel() {
    model.saving = true;
    try {
      const d = await requestAgentApi<any>('/model-connection', { method: 'PUT', body: JSON.stringify(model.form) });
      model.hasKey = !!d.has_api_key;
      model.form.api_key = '';
      model.feedback = { success: true, message: '已保存，下一次模型请求生效' };
      window.dispatchEvent(new Event('axiom:model-config-updated'));
    } catch (e: any) { model.feedback = fail(e, '保存失败'); }
    finally { model.saving = false; }
  }
  async function testModel() {
    model.testing = true;
    model.feedback = null;
    try { model.feedback = await requestAgentApi<Result>('/model-connection/test', { method: 'POST', body: JSON.stringify(model.form) }); }
    catch (e: any) { model.feedback = fail(e, '测试失败'); }
    finally { model.testing = false; }
  }

  // ---- 向量模型 ----
  const emb = reactive({
    loading: true, saving: false, testing: false, busy: false,
    masked: '', dimension: null as number | null,
    feedback: null as Result | null,
    form: { base_url: '', model: '', api_key: '' },
  });
  watch(() => [emb.saving, emb.testing], () => { emb.busy = emb.saving || emb.testing; });

  async function loadEmbedding() {
    emb.loading = true;
    try {
      const d = await requestAgentApi<any>('/embedding-config');
      Object.assign(emb.form, { base_url: d.base_url || '', model: d.model || '', api_key: '' });
      emb.masked = d.api_key_masked || '';
      emb.dimension = d.dimension ?? null;
    } catch (e: any) { emb.feedback = fail(e, '配置加载失败'); }
    finally { emb.loading = false; }
  }
  async function saveEmbedding() {
    emb.saving = true;
    try {
      // 后端约定：api_key 为空表示保持原密钥
      const body: Record<string, unknown> = { model: emb.form.model, base_url: emb.form.base_url };
      if (emb.form.api_key) body.api_key = emb.form.api_key;
      await requestAgentApi('/embedding-config', { method: 'PUT', body: JSON.stringify(body) });
      emb.form.api_key = '';
      emb.feedback = { success: true, message: '已保存' };
      await loadEmbedding();
    } catch (e: any) { emb.feedback = fail(e, '保存失败'); }
    finally { emb.saving = false; }
  }
  async function testEmbedding() {
    emb.testing = true;
    emb.feedback = null;
    try { emb.feedback = await requestAgentApi<Result>('/embedding-config/test', { method: 'POST', body: JSON.stringify(emb.form) }); }
    catch (e: any) { emb.feedback = fail(e, '测试失败'); }
    finally { emb.testing = false; }
  }

  // ---- 重排模型 ----
  // 后端契约与对话模型一致：GET 返回 has_api_key，api_key 留空表示保持原密钥
  const rerank = reactive({
    loading: true, saving: false, testing: false, busy: false, hasKey: false,
    feedback: null as Result | null,
    form: { base_url: '', model: '', api_key: '', enabled: true },
  });
  watch(() => [rerank.saving, rerank.testing], () => { rerank.busy = rerank.saving || rerank.testing; });
  watch(() => ({ ...rerank.form }), () => { rerank.feedback = null; }, { deep: true });

  async function loadRerank() {
    rerank.loading = true;
    try {
      const d = await requestAgentApi<any>('/rerank-config');
      Object.assign(rerank.form, { base_url: d.base_url, model: d.model, api_key: '', enabled: d.has_api_key ? d.enabled : true });
      rerank.hasKey = !!d.has_api_key;
    } catch (e: any) { rerank.feedback = fail(e, '配置加载失败'); }
    finally { rerank.loading = false; }
  }
  async function saveRerank() {
    rerank.saving = true;
    try {
      const d = await requestAgentApi<any>('/rerank-config', { method: 'PUT', body: JSON.stringify(rerank.form) });
      rerank.hasKey = !!d.has_api_key;
      rerank.form.api_key = '';
      rerank.feedback = { success: true, message: '已保存，下一次检索生效' };
    } catch (e: any) { rerank.feedback = fail(e, '保存失败'); }
    finally { rerank.saving = false; }
  }
  async function testRerank() {
    rerank.testing = true;
    rerank.feedback = null;
    try { rerank.feedback = await requestAgentApi<Result>('/rerank-config/test', { method: 'POST', body: JSON.stringify(rerank.form) }); }
    catch (e: any) { rerank.feedback = fail(e, '测试失败'); }
    finally { rerank.testing = false; }
  }

  // ---- 联网搜索 ----
  const search = reactive({
    loading: true, saving: false, testing: false, busy: false,
    feedback: null as Result | null,
    form: { enabled: false, searxngUrl: '', searxngEngines: '', rerankerProvider: 'none' },
  });
  // 这页只暴露这两个；其余 provider 的取值原样保留，保存时不会被覆盖成 none
  const SIMPLE_RERANKERS = ['none', 'platform'];
  watch(() => [search.saving, search.testing], () => { search.busy = search.saving || search.testing; });

  async function loadSearch() {
    search.loading = true;
    try {
      const d = await requestAgentApi<any>('/platform-config/web-search');
      Object.assign(search.form, {
        enabled: !!d.enabled,
        searxngUrl: d.searxngUrl || '',
        searxngEngines: d.searxngEngines || '',
        rerankerProvider: d.rerankerProvider || 'none',
      });
    } catch (e: any) { search.feedback = fail(e, '配置加载失败'); }
    finally { search.loading = false; }
  }
  async function saveSearch() {
    search.saving = true;
    try {
      // 后端 save_web_search 是合并语义，只传这几项不会清掉其余配置
      await requestAgentApi('/platform-config/web-search', { method: 'PUT', body: JSON.stringify(search.form) });
      search.feedback = { success: true, message: '已保存' };
    } catch (e: any) { search.feedback = fail(e, '保存失败'); }
    finally { search.saving = false; }
  }
  async function testSearch() {
    search.testing = true;
    search.feedback = null;
    try {
      const r = await requestAgentApi<any>('/platform-config/web-search/test', {
        method: 'POST',
        body: JSON.stringify({ searchProvider: 'searxng', searxngUrl: search.form.searxngUrl }),
      });
      search.feedback = { success: r.status === 'ok' || r.success === true, message: r.message || '测试完成' };
    } catch (e: any) { search.feedback = fail(e, '测试失败'); }
    finally { search.testing = false; }
  }

  // ---- 校园百事通 ----
  // 后端契约：PUT /draft 必填 expected_revision + model_id；POST /draft/publish 必填
  // expected_revision（乐观并发：与服务端当前版本不一致即拒绝，避免覆盖他人改动）。
  const campus = reactive({
    loading: true, saving: false, publishing: false, checking: false, busy: false,
    error: '', enabled: false, revision: 0, draftStatus: '',
    modelId: '', models: [] as any[],
    // bindings 存知识库 id；knowledgeBases 是可勾选的候选（当前管理员名下的库）
    bindings: [] as string[], knowledgeBases: [] as any[],
    domains: '', note: '', issues: [] as string[],
    feedback: null as Result | null,
  });
  watch(
    () => [campus.saving, campus.publishing, campus.checking],
    () => { campus.busy = campus.saving || campus.publishing || campus.checking; }
  );

  function modelValue(m: any): string {
    return String(typeof m === 'string' ? m : m?.value ?? m?.id ?? m?.model ?? '');
  }
  function modelLabel(m: any): string {
    return String(typeof m === 'string' ? m : m?.label ?? m?.name ?? modelValue(m));
  }

  async function loadCampus() {
    campus.loading = true;
    campus.error = '';
    try {
      const d = await requestAgentApi<any>('/campus-assistant/admin/config');
      campus.enabled = !!d.enabled;
      campus.revision = Number(d.revision ?? 0);
      campus.models = Array.isArray(d.available_models) ? d.available_models : [];
      const draft = d.draft || {};
      campus.draftStatus = draft.status || '';
      campus.modelId = String(draft.model_id || '');
      // 服务端存的是 {host, include_subdomains} 对象，页面上按「每行一个域名」展示
      const domains = draft.official_domains;
      campus.domains = Array.isArray(domains)
        ? domains.map((d: any) => String(typeof d === 'string' ? d : d?.host ?? '')).filter(Boolean).join('\n')
        : '';
      const bindings = draft.knowledge_bindings;
      campus.bindings = Array.isArray(bindings)
        ? bindings.map((b: any) => String(b?.knowledge_id ?? b)).filter(Boolean)
        : [];
      campus.note = draft.change_note || '';
      const kbs = await requestAgentApi<any[]>('/knowledge/bases?scope=owned');
      campus.knowledgeBases = Array.isArray(kbs) ? kbs : [];
    } catch (e: any) { campus.error = e?.message || '配置加载失败'; }
    finally { campus.loading = false; }
  }

  function draftPayload() {
    const byId = new Map(campus.knowledgeBases.map((kb: any) => [String(kb.id), kb]));
    return {
      expected_revision: campus.revision,
      model_id: campus.modelId,
      // 接口要的是 {host, include_subdomains}，且拒绝多余字段；用户只关心域名本身，
      // 子域名默认放行（学校各院系站点几乎都是子域名）
      official_domains: campus.domains
        .split('\n')
        .map((x) => x.trim().replace(/^https?:\/\//, '').replace(/\/.*$/, ''))
        .filter(Boolean)
        .map((host) => ({ host, include_subdomains: true })),
      knowledge_bindings: campus.bindings.map((id) => ({
        knowledge_id: id,
        knowledge_name_snapshot: byId.get(id)?.name ?? null,
      })),
      change_note: campus.note,
    };
  }

  /** 把 /draft/validate 的结果转成界面上的待办清单。 */
  async function checkCampus(silent = false): Promise<boolean> {
    if (!silent) campus.checking = true;
    try {
      const r = await requestAgentApi<any>('/campus-assistant/admin/draft/validate', {
        method: 'POST',
        body: JSON.stringify({}),
      });
      campus.issues = Array.isArray(r?.errors) ? r.errors : [];
      if (!silent) {
        campus.feedback = campus.issues.length
          ? { success: false, message: `还有 ${campus.issues.length} 项未满足，见下方清单` }
          : { success: true, message: '配置完整，可以发布' };
      }
      return campus.issues.length === 0;
    } catch (e: any) {
      if (!silent) campus.feedback = fail(e, '检查失败');
      return false;
    } finally { campus.checking = false; }
  }

  async function saveCampusDraft() {
    if (!campus.modelId) {
      campus.feedback = { success: false, message: '请先选择对话模型' };
      return;
    }
    campus.saving = true;
    campus.feedback = null;
    try {
      await requestAgentApi('/campus-assistant/admin/draft', {
        method: 'PUT',
        body: JSON.stringify(draftPayload()),
      });
      campus.feedback = { success: true, message: '草稿已保存，发布后对用户生效' };
      await loadCampus();
      await checkCampus(true);
    } catch (e: any) { campus.feedback = fail(e, '保存失败'); }
    finally { campus.saving = false; }
  }

  async function publishCampus() {
    campus.publishing = true;
    campus.feedback = null;
    try {
      await requestAgentApi('/campus-assistant/admin/draft/publish', {
        method: 'POST',
        body: JSON.stringify({ expected_revision: campus.revision, change_note: campus.note }),
      });
      campus.feedback = { success: true, message: '已发布，用户侧即刻生效' };
      campus.issues = [];
      await loadCampus();
    } catch (e: any) {
      campus.feedback = fail(e, '发布失败');
      await checkCampus(true);
    }
    finally { campus.publishing = false; }
  }

  // ---- 用户 ----
  const users = reactive({ loading: true, error: '', list: [] as any[] });
  async function loadUsers() {
    users.loading = true;
    users.error = '';
    try {
      const d: any = await defHttp.get({ url: '/sys/user/list', params: { pageNo: 1, pageSize: 200 } });
      users.list = d?.records || d?.result?.records || [];
    } catch (e: any) { users.error = e?.message || '加载失败'; }
    finally { users.loading = false; }
  }

  onMounted(() => {
    if (!allowed) {
      router.replace('/center/chat');
      return;
    }
    loadModel();
    loadEmbedding();
    loadRerank();
    loadSearch();
    // 打开即显示校园百事通还差哪些配置，不用等到点发布才知道
    loadCampus().then(() => checkCampus(true));
    loadUsers();
  });
</script>

<style scoped>
  .admin-page { width: 100%; max-width: 860px; margin: 0 auto; padding: 38px 32px 64px; color: #18181b; }
  .page-heading { margin-bottom: 26px; }
  .check-list { display: flex; flex-direction: column; gap: 8px; margin: 4px 0 6px; }
  .check-item { display: flex; align-items: center; gap: 10px; margin: 0; font-weight: 450; cursor: pointer; }
  .check-item input { width: auto; margin: 0; }
  .check-item small { color: #85858f; font-size: 12px; }
  .back { display: inline-flex; align-items: center; gap: 6px; margin-bottom: 18px; padding: 0;
          font-size: 13px; color: #85858f; background: none; border: 0; cursor: pointer;
          transition: color 0.15s; }
  .back:hover { color: #18181b; }
  h1 { margin: 0 0 8px; font-size: 24px; font-weight: 650; letter-spacing: -0.6px; }
  p { margin: 0; color: #85858f; font-size: 13px; line-height: 1.7; }
  .tabs { display: flex; gap: 22px; margin-bottom: 22px; border-bottom: 1px solid #e5e5ea; overflow-x: auto; }
  .tab { padding: 0 0 10px; font-size: 14px; color: #85858f; background: none; border: 0;
         border-bottom: 2px solid transparent; cursor: pointer; white-space: nowrap; }
  .tab.active { color: #18181b; font-weight: 500; border-bottom-color: #18181b; }
  .card { padding: 30px; border: 1px solid #e5e5ea; border-radius: 16px; background: #fff; }
  .card + .card { margin-top: 18px; }
  h2 { margin: 0; font-size: 15px; font-weight: 600; }
  label { display: flex; align-items: center; gap: 10px; margin: 22px 0 9px; font-size: 14px; font-weight: 550; }
  label:first-of-type { margin-top: 0; }
  .ok { font-size: 12px; font-weight: 400; color: #238257; }
  input, textarea { width: 100%; padding: 0 13px; color: #27272a; border: 1px solid #dedee5;
                    border-radius: 9px; background: #fff; outline: none; transition: border-color 0.15s; font: inherit; }
  input { height: 44px; }
  textarea { padding: 11px 13px; line-height: 1.6; resize: vertical; }
  input:focus, textarea:focus { border-color: #71717a; box-shadow: 0 0 0 3px #18181b08; }
  input::placeholder, textarea::placeholder { color: #a1a1aa; }
  .hint { margin-top: 7px; font-size: 12px; }
  .switch-row { display: flex; align-items: center; justify-content: space-between; gap: 20px;
                margin-top: 28px; padding-top: 22px; border-top: 1px solid #eeeef1; }
  .switch-row.first { margin-top: 0; padding-top: 0; border-top: 0; }
  .switch-row strong { font-size: 14px; font-weight: 550; }
  .switch-row p { margin-top: 3px; font-size: 12px; }
  .status { display: flex; flex-wrap: wrap; gap: 28px; margin: 0 0 24px; }
  .status div { margin: 0; }
  dt { font-size: 12px; color: #85858f; }
  dd { margin: 4px 0 0; font-size: 14px; font-weight: 550; }
  select { width: 100%; height: 44px; padding: 0 11px; font: inherit; color: #27272a;
           background: #fff; border: 1px solid #dedee5; border-radius: 9px; outline: none; }
  select:focus { border-color: #71717a; box-shadow: 0 0 0 3px #18181b08; }
  .issue-list { margin: 8px 0 0; padding-left: 18px; }
  .issue-list li { margin-top: 4px; }
  .user-table { width: 100%; margin-top: 16px; border-collapse: collapse; font-size: 13px; }
  .user-table th { padding: 9px 8px; color: #85858f; font-weight: 500; text-align: left; border-bottom: 1px solid #e5e5ea; }
  .user-table td { padding: 11px 8px; border-bottom: 1px solid #f2f2f5; }
  .actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 22px; }
  button { padding: 10px 18px; font-size: 13px; font-weight: 550; border-radius: 9px; cursor: pointer; }
  .primary { color: #fff; background: #151923; border: 1px solid #151923; }
  .secondary { color: #3f3f46; background: #fff; border: 1px solid #dedee5; }
  button:disabled { cursor: wait; opacity: 0.55; }
  button:focus-visible { outline: 2px solid #71717a; outline-offset: 3px; }
  .feedback { margin-top: 18px; padding: 13px 16px; border-radius: 9px; font-size: 13px; }
  .success { color: #23704c; background: #f0f8f3; }
  .error { color: #a13737; background: #fff2f2; }
  .muted { padding: 40px; color: #85858f; text-align: center; }
  @media (max-width: 640px) {
    .admin-page { padding: 24px 16px 48px; }
    .card { padding: 20px; }
    .actions { flex-direction: column-reverse; }
    .actions button { width: 100%; }
  }
</style>
