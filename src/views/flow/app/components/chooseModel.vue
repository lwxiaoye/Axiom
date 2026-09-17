<template>
  <a-modal
    v-model:open="open"
    :width="760"
    :title="headerTitle"
    :footer="null"
    :maskClosable="false"
    :destroyOnClose="true"
    :centered="true"
    wrapClassName="add-application-modal"
    @cancel="closeModal"
  >
    <div class="application-form-wrap">
      <h2>{{ pageTitle }}</h2>
      <p class="form-subtitle">{{ pageSubtitle }}</p>

      <a-form ref="formRef" :model="formState" :rules="rules" layout="vertical" :disabled="isDetail">
        <a-form-item v-if="formState.appType === 'agent'" label="应用来源">
          <div class="ai-kind-readonly">
            <span class="ai-kind-badge">智能体应用</span>
            <span>由“我的智能体”发布生成，不能在应用管理中手工创建或变更来源。</span>
          </div>
        </a-form-item>

        <a-form-item v-if="formState.appType === 'agent'" label="智能体类型" name="formOptions">
          <div class="ai-kind-readonly">
            <span class="ai-kind-badge">{{ aiAppKindLabel }}</span>
            <span>由“我的智能体”发布生成，应用管理仅维护门户基础信息、启停状态和可见权限。</span>
          </div>
        </a-form-item>

        <a-form-item v-else label="应用来源" name="appType" required>
          <a-radio-group v-model:value="formState.appType" button-style="solid">
            <a-radio-button value="custom">自建业务应用</a-radio-button>
            <a-radio-button value="external">外部接入应用</a-radio-button>
          </a-radio-group>
        </a-form-item>

        <div class="form-grid">
          <a-form-item label="应用名称" name="appName" required>
            <a-input v-model:value="formState.appName" placeholder="请输入应用名称" />
          </a-form-item>

          <a-form-item label="终端类型" name="terminalType" required>
            <JSelectMultiple v-model:value="formState.terminalType" dictCode="terminal_type" placeholder="请选择终端类型" />
          </a-form-item>

          <a-form-item label="能力分类" name="appCategory" required>
            <a-select
              v-model:value="formState.appCategory"
              :options="APP_CAPABILITY_OPTIONS"
              placeholder="请选择能力分类"
              showSearch
              allowClear
              optionFilterProp="label"
              :style="{ width: '100%' }"
            />
          </a-form-item>

          <a-form-item label="状态" name="status" required>
            <JDictSelectTag v-model:value="formState.status" dictCode="status" type="radio" />
          </a-form-item>

          <a-form-item label="应用角色权限" name="selectedRoles">
            <ApiSelect
              v-model:value="selectedRoleValues"
              mode="multiple"
              :api="getAllRolesListNoByTenant"
              labelField="roleName"
              valueField="id"
              placeholder="请选择应用角色权限"
              :immediate="true"
            />
          </a-form-item>

          <a-form-item label="应用部门权限" name="selectedDeparts">
            <JSelectDept
              v-model:value="selectedDepartValues"
              placeholder="请选择应用部门权限"
              :multiple="true"
              :sync="false"
              :checkStrictly="true"
              :defaultExpandLevel="2"
            />
          </a-form-item>

          <a-form-item label="H5 跳转地址" name="h5Url">
            <a-input v-model:value="formState.h5Url" placeholder="请输入 H5 跳转地址" />
          </a-form-item>

          <a-form-item label="PC 跳转地址" name="pcUrl">
            <a-input v-model:value="formState.pcUrl" placeholder="请输入 PC 跳转地址" />
          </a-form-item>
        </div>

        <div class="form-grid asset-order-grid">
          <a-form-item label="应用图标" name="appIcon">
            <div class="icon-source-field">
              <a-radio-group
                v-model:value="iconSource"
                class="icon-source-switch"
                button-style="solid"
                :disabled="isDetail"
                @change="handleIconSourceChange"
              >
                <a-radio-button value="upload">上传图片</a-radio-button>
                <a-radio-button value="url">网络图片</a-radio-button>
              </a-radio-group>

              <div v-if="iconSource === 'upload'" class="icon-upload-row">
                <JUpload
                  v-model:value="uploadedAppIcon"
                  class="app-icon-uploader"
                  fileType="image"
                  text="上传图片"
                  :maxCount="1"
                  :multiple="false"
                  :disabled="isDetail"
                />
                <div class="upload-tips">
                  <div>支持常见图片格式。</div>
                  <div>建议比例 1:1，大小不超过 2MB。</div>
                </div>
              </div>

              <div v-else class="icon-url-row">
                <a-input
                  v-model:value="networkAppIcon"
                  placeholder="请输入以 http:// 或 https:// 开头的图片地址"
                  :disabled="isDetail"
                  allow-clear
                />
                <div v-if="networkIconPreviewUrl" class="network-icon-preview">
                  <a-image
                    v-if="!networkIconLoadFailed"
                    :src="networkIconPreviewUrl"
                    :width="92"
                    :height="92"
                    alt="应用图标预览"
                    @load="networkIconLoadFailed = false"
                    @error="networkIconLoadFailed = true"
                  />
                  <div v-else class="network-icon-error">图片加载失败，请检查地址</div>
                </div>
                <div class="upload-tips">请确保图片地址可公开访问且长期有效。</div>
              </div>
            </div>
          </a-form-item>

          <div class="order-open-type-stack">
            <a-form-item label="排序码" name="orderNum">
              <a-input-number v-model:value="formState.orderNum" placeholder="请输入排序码" :min="0" :precision="0" />
            </a-form-item>

            <a-form-item label="打开方式" name="openType" required>
              <a-radio-group v-model:value="formState.openType">
                <a-radio v-for="option in APP_OPEN_TYPE_OPTIONS" :key="option.value" :value="option.value">
                  {{ option.label }}
                </a-radio>
              </a-radio-group>
            </a-form-item>
          </div>
        </div>

        <a-form-item label="应用描述" name="appRemark">
          <a-textarea v-model:value="formState.appRemark" placeholder="请输入应用描述" :rows="5" />
        </a-form-item>
      </a-form>
    </div>

    <div class="modal-footer">
      <a-button @click="closeModal">取消</a-button>
      <a-button v-if="!isDetail" type="primary" :loading="submitLoading" @click="handleOk">完成</a-button>
    </div>
  </a-modal>
</template>

<script lang="ts" setup>
  import { computed, reactive, ref } from 'vue';
  import type { FormInstance } from 'ant-design-vue';
  import { ApiSelect, JDictSelectTag, JSelectDept, JSelectMultiple } from '/@/components/Form';
  import JUpload from '/@/components/Form/src/jeecg/components/JUpload/JUpload.vue';
  import { toStaticFileStoragePath } from '/@/utils/common/fileUrl';
  import { getAllRolesListNoByTenant } from '@/views/system/user/user.api';
  import { queryAppDept, queryAppRole, saveOrUpdate } from '../AppInfo.api';
  import { APP_CAPABILITY_OPTIONS, isAppCapabilityValue } from '../appCapabilityOptions';
  import {
    APP_OPEN_TYPE_OPTIONS,
    getAiAppKind,
    getAiAppKindLabel,
    normalizeAppOpenType,
    serializeAiAppOptions,
    type AiAppKind,
  } from '@/views/workflow/shared/agentApp';

  const emit = defineEmits(['success']);

  const open = ref(false);
  const isDetail = ref(false);
  const submitLoading = ref(false);
  const formRef = ref<FormInstance>();
  const iconSource = ref<'upload' | 'url'>('upload');
  const uploadedIconCache = ref('');
  const networkIconCache = ref('');
  const networkIconLoadFailed = ref(false);

  const emptyFormState = () => ({
    id: undefined,
    appName: '',
    terminalType: undefined,
    appCategory: undefined,
    status: '1',
    openType: '_blank',
    h5Url: '',
    pcUrl: '',
    baseUrl: '',
    apiKey: '',
    isRecommend: 'N',
    appRemark: '',
    appIcon: '',
    selectedRoles: '',
    selectedDeparts: '',
    orderNum: undefined,
    appType: 'custom',
    processDefinitionKey: undefined,
    formOptions: undefined,
    rule: undefined,
  });

  const formState = reactive<any>(emptyFormState());

  function validateAppCategory(_rule: unknown, value: unknown) {
    return isAppCapabilityValue(value)
      ? Promise.resolve()
      : Promise.reject(new Error('请选择智能体广场使用的标准能力分类'));
  }

  const rules = {
    appName: [{ required: true, message: '请输入应用名称', trigger: 'blur' }],
    terminalType: [{ required: true, message: '请选择终端类型', trigger: 'change' }],
    appCategory: [
      { required: true, message: '请选择能力分类', trigger: 'change' },
      { validator: validateAppCategory, trigger: 'change' },
    ],
    status: [{ required: true, message: '请选择状态', trigger: 'change' }],
    appIcon: [{ validator: validateAppIconUrl, trigger: 'blur' }],
    appType: [{ required: true, message: '请选择应用来源', trigger: 'change' }],
  };

  const headerTitle = computed(() => (formState.id ? '应用编辑' : '新增应用'));
  const pageTitle = computed(() => (formState.id ? (isDetail.value ? '应用详情' : '编辑应用') : '新增应用'));
  const pageSubtitle = computed(() =>
    formState.id ? '请维护应用系统资源的基础信息、启停状态和可见权限。' : '新增应用默认为自建业务应用，可按实际交付方式切换为外部接入应用。'
  );
  const aiAppKindLabel = computed(() => getAiAppKindLabel(formState));

  function toCommaSeparatedValue(value: unknown) {
    if (Array.isArray(value)) {
      return value.filter(Boolean).join(',');
    }
    if (typeof value === 'string' && value) {
      return value
        .split(',')
        .filter(Boolean)
        .join(',');
    }
    return '';
  }

  function toMultipleValues(value: unknown) {
    if (Array.isArray(value)) {
      return value.filter(Boolean);
    }
    if (typeof value === 'string' && value) {
      return value.split(',').filter(Boolean);
    }
    return [];
  }

  const selectedRoleValues = computed({
    get: () => toMultipleValues(formState.selectedRoles),
    set: (value) => {
      formState.selectedRoles = toCommaSeparatedValue(value);
    },
  });

  const selectedDepartValues = computed({
    get: () => toMultipleValues(formState.selectedDeparts),
    set: (value) => {
      formState.selectedDeparts = toCommaSeparatedValue(value);
    },
  });

  const uploadedAppIcon = computed({
    get: () => uploadedIconCache.value,
    set: (value) => {
      uploadedIconCache.value = value || '';
      if (iconSource.value === 'upload') {
        formState.appIcon = uploadedIconCache.value;
      }
    },
  });

  const networkAppIcon = computed({
    get: () => networkIconCache.value,
    set: (value) => {
      networkIconCache.value = value || '';
      networkIconLoadFailed.value = false;
      if (iconSource.value === 'url') {
        formState.appIcon = networkIconCache.value.trim();
      }
    },
  });

  const networkIconPreviewUrl = computed(() => {
    const url = networkIconCache.value.trim();
    return isNetworkImageUrl(url) ? url : '';
  });

  function isNetworkImageUrl(value: unknown) {
    return /^https?:\/\/\S+$/i.test(String(value || '').trim());
  }

  async function validateAppIconUrl() {
    if (iconSource.value === 'url' && formState.appIcon && !isNetworkImageUrl(formState.appIcon)) {
      return Promise.reject('请输入有效的 http:// 或 https:// 图片地址');
    }
    return Promise.resolve();
  }

  function handleIconSourceChange() {
    formState.appIcon = iconSource.value === 'url' ? networkIconCache.value.trim() : uploadedIconCache.value;
    networkIconLoadFailed.value = false;
    setTimeout(() => formRef.value?.clearValidate?.('appIcon'), 0);
  }

  function resetFormState(record?: Recordable) {
    Object.assign(formState, emptyFormState(), record || {});
    formState.status = formState.status || '1';
    formState.appCategory = isAppCapabilityValue(formState.appCategory) ? formState.appCategory : undefined;
    formState.isRecommend = formState.isRecommend || 'N';
    formState.appType = normalizeAppSourceType(formState.appType);
    formState.openType = normalizeAppOpenType(formState.openType);
    if (formState.appType === 'agent') {
      formState.formOptions = serializeAiAppOptions(getAiAppKind(formState), formState.formOptions);
    }
    formState.selectedRoles = toCommaSeparatedValue(formState.selectedRoles);
    formState.selectedDeparts = toCommaSeparatedValue(formState.selectedDeparts);
    const appIcon = toStaticFileStoragePath(String(formState.appIcon || '').trim());
    formState.appIcon = appIcon;
    if (isNetworkImageUrl(appIcon)) {
      iconSource.value = 'url';
      networkIconCache.value = appIcon;
      uploadedIconCache.value = '';
    } else {
      iconSource.value = 'upload';
      uploadedIconCache.value = appIcon;
      networkIconCache.value = '';
    }
    networkIconLoadFailed.value = false;
  }

  function normalizeAppSourceType(value: unknown) {
    const source = String(value || '').trim().toLowerCase();
    if (source === 'agent' || source === 'ai') {
      return 'agent';
    }
    if (source === 'custom' || source === 'internal' || source === 'flow') {
      return 'custom';
    }
    if (source === 'external') return 'external';
    return 'custom';
  }

  async function init(data: any, detail: boolean, defaultAiAppKind?: AiAppKind) {
    isDetail.value = !!detail;
    const record = data ? { ...data } : undefined;
    if (record?.id) {
      const [selectedRoles, selectedDeparts] = await Promise.all([
        queryAppRole({ appId: record.id }),
        queryAppDept({ appId: record.id }),
      ]);
      record.selectedRoles = toCommaSeparatedValue(selectedRoles);
      record.selectedDeparts = toCommaSeparatedValue(selectedDeparts);
    }
    resetFormState(record);
    if (defaultAiAppKind) {
      formState.appType = 'agent';
      formState.formOptions = serializeAiAppOptions(defaultAiAppKind, formState.formOptions);
    }
    open.value = true;
    setTimeout(() => formRef.value?.clearValidate?.(), 0);
  }

  async function handleOk() {
    await formRef.value?.validate();
    submitLoading.value = true;
    try {
      await saveOrUpdate({
        ...formState,
        selectedRoles: toCommaSeparatedValue(formState.selectedRoles),
        selectedDeparts: toCommaSeparatedValue(formState.selectedDeparts),
      });
      emit('success');
      closeModal();
    } finally {
      submitLoading.value = false;
    }
  }

  function closeModal() {
    open.value = false;
  }

  defineExpose({
    init,
  });
</script>

<style lang="less">
  .add-application-modal {
    .ant-modal-content {
      overflow: hidden;
    }

    .ant-modal-header {
      border-bottom: 1px solid #d9dde6;
    }

    .ant-modal-body {
      padding: 0;
      background: #fff;
    }
  }
</style>

<style lang="less" scoped>
  .application-form-wrap {
    max-height: calc(100vh - 180px);
    overflow-y: auto;
    padding: 28px 26px 18px;

    h2 {
      margin: 0 0 8px;
      color: #111827;
      font-size: 26px;
      font-weight: 800;
      line-height: 1.15;
    }
  }

  .form-subtitle {
    margin: 0 0 22px;
    color: #6b7280;
    font-size: 12px;
  }

  .form-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    column-gap: 20px;
  }

  .ai-kind-readonly {
    display: flex;
    align-items: center;
    gap: 10px;
    min-height: 40px;
    padding: 10px 12px;
    color: #4b5563;
    font-size: 12px;
    line-height: 1.6;
    border: 1px solid #d9dde6;
    border-radius: 8px;
    background: #f8fafc;
  }

  .ai-kind-badge {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 76px;
    min-height: 26px;
    padding: 0 10px;
    flex: 0 0 auto;
    color: #111827;
    font-size: 12px;
    font-weight: 700;
    border-radius: 999px;
    background: #fff;
    box-shadow: inset 0 0 0 1px #d9dde6;
  }

  :deep(.ant-form-item) {
    margin-bottom: 18px;
  }

  :deep(.ant-form-item-label > label) {
    color: #111827;
    font-size: 13px;
    font-weight: 600;
  }

  :deep(.ant-form-item-required::before) {
    color: #d93025 !important;
  }

  :deep(.ant-input),
  :deep(.ant-select-selector) {
    min-height: 40px;
    border-color: #cfd6e4 !important;
    border-radius: 3px !important;
    box-shadow: none !important;
  }

  :deep(.ant-select-single .ant-select-selector) {
    display: flex;
    align-items: center;
  }

  :deep(.ant-select-single .ant-select-selection-item),
  :deep(.ant-select-single .ant-select-selection-placeholder) {
    line-height: normal;
  }

  :deep(.ant-select-selection-placeholder),
  :deep(.ant-input::placeholder),
  :deep(textarea.ant-input::placeholder) {
    color: #9ca3af;
    font-size: 12px;
  }

  :deep(textarea.ant-input) {
    min-height: 92px;
    resize: none;
    border-color: #cfd6e4;
    border-radius: 3px;
    box-shadow: none;
  }

  .icon-upload-row {
    display: flex;
    align-items: center;
    gap: 18px;
  }

  .icon-source-field {
    display: flex;
    flex-direction: column;
    gap: 14px;
  }

  .icon-source-switch {
    align-self: flex-start;

    :deep(.ant-radio-button-wrapper) {
      font-size: 12px;
    }
  }

  .icon-url-row {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .network-icon-preview {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 104px;
    height: 104px;
    overflow: hidden;
    border: 1px solid #d8dee9;
    border-radius: 4px;
    background: #f8fafc;

    :deep(.ant-image),
    :deep(.ant-image-img) {
      width: 92px !important;
      height: 92px !important;
    }

    :deep(.ant-image-img) {
      object-fit: contain;
      border-radius: 3px;
    }
  }

  .network-icon-error {
    padding: 12px;
    color: #d93025;
    font-size: 12px;
    line-height: 1.5;
    text-align: center;
  }

  .asset-order-grid {
    align-items: start;

    :deep(.ant-input-number) {
      width: 100%;
    }
  }

  .order-open-type-stack {
    display: flex;
    flex-direction: column;

    :deep(.ant-form-item:last-child) {
      margin-bottom: 0;
    }
  }

  .app-icon-uploader {
    width: 104px;
    flex: 0 0 auto;

    :deep(.ant-upload.ant-upload-select-picture-card) {
      width: 104px;
      height: 92px;
      margin: 0;
      border: 1px dashed #b9c7db;
      border-radius: 3px;
      background: #fff;
    }

    :deep(.ant-upload-text) {
      margin-top: 8px;
      color: #4b5563;
      font-size: 12px;
    }
  }

  .upload-tips {
    color: #374151;
    font-size: 12px;
    line-height: 1.8;
  }

  .modal-footer {
    display: flex;
    justify-content: flex-end;
    gap: 12px;
    padding: 18px 24px;
    border-top: 1px solid #d9dde6;
    background: #f4f4f4;

    :deep(.ant-btn) {
      min-width: 82px;
      height: 34px;
      border-radius: 8px;
      font-size: 13px;
    }

    :deep(.ant-btn-primary) {
      border-color: #005bac;
      background: #005bac;
      box-shadow: 0 4px 10px rgba(0, 91, 172, 0.22);
    }
  }

  @media (max-width: 768px) {
    .form-grid {
      grid-template-columns: 1fr;
    }

    .ai-kind-readonly {
      align-items: flex-start;
      flex-direction: column;
    }
  }
</style>
