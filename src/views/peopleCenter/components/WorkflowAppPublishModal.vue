<template>
  <a-modal
    v-model:open="open"
    :width="720"
    :title="modalTitle"
    :footer="null"
    :destroyOnClose="true"
    :maskClosable="false"
    wrapClassName="workflow-app-acl-modal"
    @cancel="close"
  >
    <div class="workflow-app-acl-body">
      <div class="acl-heading">
        <div>
          <strong>{{ isUpdate ? '更新已发布智能体' : '发布到智能体广场' }}</strong>
          <p v-if="isUpdate">提交更新后，当前线上版本会继续运行；可见范围默认沿用线上版本，新版本仅在审核通过后替换线上版本。</p>
          <p v-else>可按角色和部门限定发布后的可见范围；未选择则仅自己可用，提交后进入后台审核。</p>
        </div>
      </div>

      <div class="acl-list">
        <div class="acl-row publish-row">
          <span class="publish-label">可见角色</span>
          <JSelectRole
            v-model:value="form.visibleRoleIds"
            placeholder="可选，不选则仅自己可用"
            button-text="选择"
            @update:value="markRoleVisibilityTouched"
          />
        </div>
        <div class="acl-row publish-row">
          <span class="publish-label">可见部门</span>
          <JSelectDept
            v-model:value="form.visibleDeptIds"
            placeholder="可选，不选则仅自己可用"
            button-text="选择"
            @update:value="markDeptVisibilityTouched"
          />
        </div>
        <label class="publish-note">
          <span>发布说明</span>
          <a-textarea
            v-model:value="form.changeNote"
            :rows="3"
            :maxlength="200"
            show-count
            placeholder="可选，说明本次发布的变更内容"
          />
        </label>

        <div class="route-meta-heading">
          <strong>能力发现（可选）</strong>
          <p>帮助主对话按语义找到本智能体：审核通过上线后生效，不影响审核中的线上版本。</p>
        </div>
        <label class="publish-note">
          <span>能力描述</span>
          <a-textarea
            v-model:value="form.routeDescription"
            :rows="2"
            :maxlength="1000"
            show-count
            placeholder="默认使用应用简介；描述“什么任务该交给它”"
          />
        </label>
        <label class="publish-note">
          <span>触发示例</span>
          <a-textarea
            v-model:value="form.triggerExamples"
            :rows="3"
            placeholder="每行一个「应该调用」的用户表达，最多 20 条、单条 200 字内"
          />
        </label>
        <label class="publish-note">
          <span>不适用示例</span>
          <a-textarea
            v-model:value="form.negativeExamples"
            :rows="2"
            placeholder="每行一个「不应调用」的用户表达，最多 20 条、单条 200 字内"
          />
        </label>
        <div class="acl-row publish-row">
          <span class="publish-label">业务标签</span>
          <a-select
            v-model:value="form.tags"
            mode="tags"
            :max-tag-count="6"
            :token-separators="[',', '，', ' ']"
            placeholder="回车添加，最多 20 个、单个 32 字内"
            :open="false"
          />
        </div>
      </div>
    </div>

    <div class="workflow-app-acl-footer">
      <a-button @click="close">取消</a-button>
      <a-button type="primary" :loading="props.loading" @click="submit">{{ isUpdate ? '提交更新' : '提交发布' }}</a-button>
    </div>
  </a-modal>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue';
import { message } from 'ant-design-vue';
import { JSelectDept, JSelectRole } from '/@/components/Form';
import {
  queryWorkflowVersionPage,
  type AiWorkflowApp,
  type RouteMetadata,
} from '../../workflow/api/workflow.api';

const props = defineProps<{
  loading?: boolean;
}>();

const emit = defineEmits<{
  (
    e: 'submit',
    payload: {
      app: Partial<AiWorkflowApp>;
      visibleRoleIds?: string[] | string;
      visibleDeptIds?: string[] | string;
      changeNote?: string;
      routeMetadata?: RouteMetadata;
    }
  ): void;
}>();

const open = ref(false);
const record = ref<Partial<AiWorkflowApp> | null>(null);
const form = reactive<{
  visibleRoleIds: string[] | string;
  visibleDeptIds: string[] | string;
  changeNote: string;
  routeDescription: string;
  triggerExamples: string;
  negativeExamples: string;
  tags: string[];
}>({
  visibleRoleIds: [],
  visibleDeptIds: [],
  changeNote: '',
  routeDescription: '',
  triggerExamples: '',
  negativeExamples: '',
  tags: [],
});

const isUpdate = computed(() => record.value?.status === 'published');

const modalTitle = computed(() => {
  const name = record.value?.name || (record.value as Recordable | null)?.appName;
  const action = isUpdate.value ? '更新发布' : '提交发布';
  return name ? `${action}: ${name}` : action;
});

let loadToken = 0;
let roleVisibilityReady = false;
let deptVisibilityReady = false;
let roleVisibilityTouched = false;
let deptVisibilityTouched = false;
// 路由字段「程序性基线」：init 默认值或 prefill 应用后的快照。提交时与当前值比对——
// 与基线一致=用户没碰过，routeMetadata 整个不传（后端继承线上版本，加载失败/线上版本
// 翻页找不到/用户抢先编辑等任何情况都不会误覆盖）；有改动才显式提交（含显式清空）。
let metaBaseline = { routeDescription: '', triggerExamples: '', negativeExamples: '', tags: '' };

function snapshotBaseline() {
  metaBaseline = {
    routeDescription: form.routeDescription,
    triggerExamples: form.triggerExamples,
    negativeExamples: form.negativeExamples,
    tags: JSON.stringify(form.tags),
  };
}

function metaTouched(): boolean {
  return (
    form.routeDescription !== metaBaseline.routeDescription ||
    form.triggerExamples !== metaBaseline.triggerExamples ||
    form.negativeExamples !== metaBaseline.negativeExamples ||
    JSON.stringify(form.tags) !== metaBaseline.tags
  );
}

function init(item: Partial<AiWorkflowApp>) {
  record.value = item;
  form.visibleRoleIds = [];
  form.visibleDeptIds = [];
  roleVisibilityReady = item.status !== 'published';
  deptVisibilityReady = item.status !== 'published';
  roleVisibilityTouched = false;
  deptVisibilityTouched = false;
  form.changeNote = '';
  // 能力描述默认应用简介（服务端召回同样以简介兜底，这里显式带出便于编辑）
  form.routeDescription = String(item.description || '');
  form.triggerExamples = '';
  form.negativeExamples = '';
  form.tags = [];
  snapshotBaseline();
  open.value = true;
  void prefillPublishedSettings(item);
}

function markRoleVisibilityTouched() {
  roleVisibilityTouched = true;
}

function markDeptVisibilityTouched() {
  deptVisibilityTouched = true;
}

async function prefillPublishedSettings(item: Partial<AiWorkflowApp>) {
  // 重新发布不清空既有路由元数据和可见范围：把当前线上版本带出为默认值。
  // 线上版本可能不在第一页（版本号降序分页），最多向后找 4 页；找不到/失败则放弃预填
  // ——此时用户不改路由字段就整个不传，由后端继承，绝不会用默认简介覆盖线上配置。
  if (!item.id) return;
  const token = ++loadToken;
  try {
    let live: {
      routeMetadata?: RouteMetadata | null;
      visibleRoleIds?: string[] | string;
      visibleDeptIds?: string[] | string;
    } | undefined;
    for (let pageNo = 1; pageNo <= 4 && !live; pageNo++) {
      const page = await queryWorkflowVersionPage({ appId: String(item.id), pageNo, pageSize: 50 });
      if (token !== loadToken || !open.value) return; // 弹窗已切换/关闭，丢弃过期结果
      if (!page?.liveVersion || !page?.records?.length) break;
      live = page.records.find((v) => v.versionNo === page.liveVersion && v.status === 'approved');
      if (!live && page.records.every((v) => v.versionNo > page.liveVersion)) continue;
      if (!live) break; // 已翻过线上版本号所在区间仍未命中，放弃
    }
    if (!live) return;
    if (!roleVisibilityTouched) {
      form.visibleRoleIds = live.visibleRoleIds || [];
      roleVisibilityReady = true;
    }
    if (!deptVisibilityTouched) {
      form.visibleDeptIds = live.visibleDeptIds || [];
      deptVisibilityReady = true;
    }
    const meta = live.routeMetadata;
    if (!meta || metaTouched()) return; // 用户已开始编辑：不覆盖用户输入，提交时按用户内容显式保存
    if (meta.routeDescription) form.routeDescription = meta.routeDescription;
    if (meta.triggerExamples?.length) form.triggerExamples = meta.triggerExamples.join('\n');
    if (meta.negativeExamples?.length) form.negativeExamples = meta.negativeExamples.join('\n');
    if (meta.tags?.length) form.tags = [...meta.tags];
    snapshotBaseline(); // 预填成功后基线=线上值：仍未触碰则不传字段，后端继承同一份
  } catch {
    // 预填失败不阻塞发布：未触碰时不传 routeMetadata，后端继承线上元数据
  }
}

function splitLines(raw: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const line of raw.split('\n')) {
    const text = line.trim();
    if (text && !seen.has(text)) {
      seen.add(text);
      out.push(text);
    }
  }
  return out;
}

function buildRouteMetadata(): RouteMetadata | undefined | null {
  // 未触碰（与程序性基线一致）→ 不传字段：后端按「未传」继承线上版本，任何加载
  // 竞态/失败都不会误覆盖。触碰过 → 显式提交本次内容，全空=用户明确要求清空。
  if (!metaTouched()) {
    return undefined;
  }
  const routeDescription = form.routeDescription.trim();
  const triggerExamples = splitLines(form.triggerExamples);
  const negativeExamples = splitLines(form.negativeExamples);
  const tags = form.tags.map((t) => String(t).trim()).filter(Boolean);
  if (triggerExamples.length > 20 || negativeExamples.length > 20) {
    message.warning('触发/不适用示例每类最多 20 条');
    return null;
  }
  if ([...triggerExamples, ...negativeExamples].some((t) => t.length > 200)) {
    message.warning('单条示例不能超过 200 字');
    return null;
  }
  if (tags.length > 20 || tags.some((t) => t.length > 32)) {
    message.warning('业务标签最多 20 个、单个 32 字内');
    return null;
  }
  return { routeDescription, triggerExamples, negativeExamples, tags };
}

function submit() {
  if (!record.value?.id) return;
  const routeMetadata = buildRouteMetadata();
  if (routeMetadata === null) return; // 校验未过，已提示
  emit('submit', {
    app: record.value,
    // 更新弹窗尚未拿到线上快照且用户未编辑时省略字段；后端以线上版本为权威继承，
    // 避免网络慢时把既有授权误提交为空数组。
    visibleRoleIds: isUpdate.value && !roleVisibilityReady && !roleVisibilityTouched ? undefined : form.visibleRoleIds,
    visibleDeptIds: isUpdate.value && !deptVisibilityReady && !deptVisibilityTouched ? undefined : form.visibleDeptIds,
    changeNote: form.changeNote.trim() || undefined,
    routeMetadata,
  });
}

function close() {
  open.value = false;
}

defineExpose({ init, close });
</script>

<style lang="less">
.workflow-app-acl-modal {
  .ant-modal-body {
    padding: 0;
  }
}
</style>

<style scoped lang="less">
.workflow-app-acl-body {
  padding: 18px 24px 10px;
}

.acl-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  margin-bottom: 12px;
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

.acl-list {
  display: grid;
  gap: 10px;
}

.acl-row {
  display: grid;
  grid-template-columns: 96px minmax(0, 1fr);
  gap: 8px;
  align-items: center;
}

.publish-label,
.publish-note > span {
  color: #334155;
  font-size: 13px;
}

.publish-note {
  display: grid;
  grid-template-columns: 96px minmax(0, 1fr);
  gap: 8px;
  align-items: flex-start;
}

.route-meta-heading {
  margin-top: 6px;
  padding: 10px 14px;
  border: 1px solid #eceef3;
  border-radius: 10px;
  background: #fafbfc;

  strong {
    color: #111827;
    font-size: 13px;
  }

  p {
    margin: 4px 0 0;
    color: #64748b;
    font-size: 12px;
    line-height: 1.7;
  }
}

.workflow-app-acl-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 12px 24px 18px;
  border-top: 1px solid #edf2f7;
}

@media (max-width: 720px) {
  .acl-heading,
  .acl-row,
  .publish-note {
    grid-template-columns: 1fr;
  }

  .acl-heading {
    display: grid;
  }
}
</style>
