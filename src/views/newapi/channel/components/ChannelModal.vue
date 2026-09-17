<template>
  <BasicModal
    destroyOnClose
    @register="registerModal"
    :canFullscreen="false"
    width="620px"
    :title="modalTitle"
    :bodyStyle="{ maxHeight: '72vh', overflowY: 'auto' }"
    wrapClassName="channel-config-modal"
  >
    <div class="channel-modal">
      <a-form ref="formRef" :model="formState" :rules="rules" layout="vertical">
        <section class="channel-section">
          <div class="section-title">
            <span class="section-icon">核</span>
            <div>
              <strong>核心配置</strong>
              <p>创建渠道所需的基本信息</p>
            </div>
          </div>

          <a-form-item label="类型" name="type" required>
            <a-select
              v-model:value="formState.type"
              show-search
              :options="channelOptions"
              placeholder="请选择渠道类型"
              :filter-option="filterOption"
            />
          </a-form-item>

          <a-form-item label="名称" name="name" required>
            <a-input v-model:value="formState.name" placeholder="请为渠道命名" />
          </a-form-item>

          <a-form-item label="密钥" name="key" :required="!isUpdate">
            <a-input-password
              v-model:value="formState.key"
              autocomplete="new-password"
              :placeholder="isUpdate ? '编辑模式下，留空则不修改已保存密钥' : '请输入渠道对应的鉴权密钥'"
            />
          </a-form-item>

          <a-form-item label="API地址" name="base_url">
            <a-input v-model:value="formState.base_url" placeholder="请输入 API 地址" />
            <p class="field-tip">
              此项可选，用于通过自定义 API 地址进行 API 调用，官方渠道一般无需填写。
            </p>
          </a-form-item>
        </section>

        <section class="channel-section">
          <div class="section-title">
            <span class="section-icon model">模</span>
            <div>
              <strong>模型配置</strong>
              <p>设置该渠道支持的模型范围</p>
            </div>
          </div>

          <a-form-item label="模型" name="models" required>
            <a-select
              v-model:value="formState.models"
              mode="tags"
              show-search
              allow-clear
              :loading="modelLoading"
              :options="modelOptions"
              placeholder="请选择该渠道所支持的模型"
            />
            <div class="model-actions">
              <a-button type="link" size="small" @click="fillMatchedModels">填入相关模型</a-button>
              <a-button type="link" size="small" danger @click="clearModels">清空所有</a-button>
            </div>
          </a-form-item>

          <a-form-item label="自定义模型名称" name="customModel">
            <a-input
              v-model:value="customModel"
              placeholder="输入自定义模型名称"
              @press-enter="insertCustomModel"
            >
              <template #suffix>
                <a-button type="link" size="small" @click="insertCustomModel">插入</a-button>
              </template>
            </a-input>
          </a-form-item>
        </section>

        <section class="channel-section">
          <div class="section-title">
            <span class="section-icon route">路</span>
            <div>
              <strong>路由与状态</strong>
              <p>配置渠道调度优先级与自动禁用策略</p>
            </div>
          </div>

          <div class="form-grid">
            <a-form-item label="优先级" name="priority">
              <a-input-number v-model:value="formState.priority" :min="0" :max="100" placeholder="请输入优先级" style="width: 100%" />
            </a-form-item>

            <a-form-item label="权重" name="weight">
              <a-input-number v-model:value="formState.weight" :min="0" :max="100" placeholder="请输入权重" style="width: 100%" />
            </a-form-item>
          </div>

          <a-form-item label="是否自动禁用" name="auto_ban">
            <a-switch v-model:checked="formState.auto_ban" checked-children="开启" un-checked-children="关闭" />
          </a-form-item>

          <a-form-item label="备注" name="remark">
            <a-textarea v-model:value="formState.remark" placeholder="请输入备注" :rows="4" />
          </a-form-item>
        </section>
      </a-form>
    </div>
    <template #footer>
      <a-button @click="handleCancel" size="large">取消</a-button>
      <a-button type="primary" :loading="loading" @click="handleSubmit" size="large">
        <template #icon>
          <CheckOutlined />
        </template>
        提交
      </a-button>
    </template>
  </BasicModal>
</template>

<script lang="ts">
  import { computed, defineComponent, reactive, ref } from 'vue';
  import type { FormInstance } from 'ant-design-vue';
  import BasicModal from '@/components/Modal/src/BasicModal.vue';
  import { useModalInner } from '@/components/Modal';
  import { CheckOutlined } from '@ant-design/icons-vue';
  import { create, getById, listModels, update } from '../channel.api';
  import { buildChannelCreatePayload, buildChannelUpdatePayload } from '../channelPayload';
  import { CHANNEL_OPTIONS } from '@/utils/channel/channel';
  import { useMessage } from '/@/hooks/web/useMessage';

  const defaultChannelForm = () => ({
    id: undefined,
    type: 1,
    name: '',
    key: '',
    base_url: '',
    models: [] as string[],
    priority: 0,
    weight: 0,
    auto_ban: true,
    remark: '',
  });

  export default defineComponent({
    name: 'ChannelModal',
    components: {
      BasicModal,
      CheckOutlined,
    },
    emits: ['success', 'register'],
    setup(_, { emit }) {
      const { createMessage } = useMessage();
      const loading = ref(false);
      const isUpdate = ref(false);
      const formRef = ref<FormInstance>();
      const formState = reactive<any>(defaultChannelForm());
      const customModel = ref('');
      const modelOptions = ref<any[]>([]);
      const modelLoading = ref(false);
      const modelsByType = ref<Record<number, string[]>>({});

      const channelOptions = CHANNEL_OPTIONS.map((item) => ({
        label: item.label,
        value: item.value,
      }));

      const rules = computed(() => ({
        type: [{ required: true, message: '请选择渠道类型', trigger: 'change' }],
        name: [{ required: true, message: '请输入渠道名称', trigger: 'blur' }],
        key: [{ required: !isUpdate.value, message: '请输入密钥', trigger: 'blur' }],
        models: [{ required: true, type: 'array', min: 1, message: '请选择或输入模型', trigger: 'change' }],
      }));

      const modalTitle = computed(() => (isUpdate.value ? '编辑模型渠道' : '创建新的渠道'));

      function filterOption(input: string, option: any) {
        return String(option?.label || '').toLowerCase().includes(input.toLowerCase());
      }

      async function loadModels() {
        modelLoading.value = true;
        try {
          const res = await listModels();
          const allModels: string[] = [];
          const nextModelsByType: Record<number, string[]> = {};

          if (res && typeof res === 'object') {
            Object.keys(res).forEach((type) => {
              const typeModels = Array.isArray(res[type]) ? res[type].filter(Boolean) : [];
              nextModelsByType[Number(type)] = typeModels;
              typeModels.forEach((model) => {
                if (!allModels.includes(model)) {
                  allModels.push(model);
                }
              });
            });
          }

          modelsByType.value = nextModelsByType;
          modelOptions.value = allModels.map((model) => ({
            label: model,
            value: model,
          }));
        } catch (error) {
          console.error('获取模型列表失败:', error);
          modelOptions.value = [];
          modelsByType.value = {};
        } finally {
          modelLoading.value = false;
        }
      }

      function resetForm(record?: Recordable) {
        Object.assign(formState, defaultChannelForm(), record || {});
        formState.type = Number(formState.type || 1);
        formState.priority = Number(formState.priority || 0);
        formState.weight = Number(formState.weight || 0);
        formState.auto_ban = formState.auto_ban === true || formState.auto_ban === 1;
        formState.models = typeof formState.models === 'string'
          ? formState.models.split(',').map((item) => item.trim()).filter(Boolean)
          : Array.isArray(formState.models)
            ? formState.models
            : [];
        customModel.value = '';
      }

      const [registerModal, { closeModal, setModalProps }] = useModalInner(async (data) => {
        setModalProps({ confirmLoading: false });
        await loadModels();
        await formRef.value?.clearValidate?.();

        if (data?.id) {
          isUpdate.value = true;
          const res = await getById(data.id);
          resetForm(res || {});
        } else {
          isUpdate.value = false;
          resetForm(data?.defaultValues || {});
        }
      });

      function fillMatchedModels() {
        const matched = modelsByType.value[Number(formState.type)] || [];
        if (matched.length === 0) {
          createMessage.warning('当前类型暂无可填入的模型列表，请先获取模型列表或手动输入');
          return;
        }
        formState.models = [...matched];
      }

      function insertCustomModel() {
        const value = customModel.value.trim();
        if (!value) {
          return;
        }
        if (!formState.models.includes(value)) {
          formState.models = [...formState.models, value];
        }
        customModel.value = '';
      }

      function clearModels() {
        formState.models = [];
        customModel.value = '';
      }

      async function handleSubmit() {
        try {
          loading.value = true;
          setModalProps({ confirmLoading: true });
          await formRef.value?.validate();

          if (isUpdate.value) {
            const values = buildChannelUpdatePayload(formState);
            await update(values);
            createMessage.success('更新成功');
            closeModal();
            emit('success', { isUpdate: true, values });
          } else {
            const params = buildChannelCreatePayload(formState);
            await create(params);
            createMessage.success('新增成功');
            closeModal();
            emit('success', { isUpdate: false, values: params.channel });
          }
        } catch (error) {
          console.error('提交失败:', error);
        } finally {
          loading.value = false;
          setModalProps({ confirmLoading: false });
        }
      }

      function handleCancel() {
        closeModal();
      }

      return {
        registerModal,
        modalTitle,
        formRef,
        formState,
        rules,
        channelOptions,
        filterOption,
        modelOptions,
        modelLoading,
        customModel,
        isUpdate,
        loading,
        loadModels,
        fillMatchedModels,
        insertCustomModel,
        clearModels,
        handleSubmit,
        handleCancel,
      };
    },
  });
</script>

<style lang="less">
  .channel-config-modal {
    .ant-modal-content {
      border-radius: 8px;
      overflow: hidden;
    }

    .ant-modal-header {
      padding: 18px 24px;
      border-bottom: 1px solid #edf0f5;
    }

    .ant-modal-title {
      color: #111827;
      font-size: 18px;
      font-weight: 800;
    }

    .ant-modal-body {
      padding: 0 20px 0 24px;
      background: #fff;
    }

    .ant-modal-footer {
      padding: 16px 24px;
      border-top: 1px solid #edf0f5;

      .ant-btn {
        min-width: 78px;
        height: 36px;
        border-radius: 8px;
        font-weight: 600;
      }
    }
  }
</style>

<style scoped lang="less">
  .channel-modal {
    padding: 16px 4px 24px 0;
  }

  .channel-section {
    padding: 8px 0 20px;
    border-bottom: 1px solid #edf0f5;

    &:last-child {
      border-bottom: 0;
    }
  }

  .section-title {
    display: flex;
    gap: 12px;
    align-items: center;
    margin-bottom: 16px;

    strong {
      display: block;
      color: #1f2937;
      font-size: 15px;
      line-height: 1.2;
    }

    p {
      margin: 4px 0 0;
      color: #6b7280;
      font-size: 12px;
    }
  }

  .section-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 34px;
    height: 34px;
    flex: 0 0 auto;
    color: #0b66d8;
    font-size: 13px;
    font-weight: 800;
    border-radius: 50%;
    background: #e8f2ff;

    &.model {
      color: #16a34a;
      background: #eaf8ee;
    }

    &.route {
      color: #7c3aed;
      background: #f4eaff;
    }
  }

  .form-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 16px;
  }

  .field-tip {
    margin: 8px 0 0;
    color: #64748b;
    font-size: 12px;
    line-height: 1.6;
  }

  .model-actions {
    display: flex;
    gap: 12px;
    margin-top: 8px;

    :deep(.ant-btn) {
      height: auto;
      padding: 0;
      font-weight: 700;
    }
  }

  :deep(.ant-form-item) {
    margin-bottom: 16px;
  }

  :deep(.ant-form-item-label > label) {
    color: #111827;
    font-size: 13px;
    font-weight: 700;
  }

  :deep(.ant-form-item-required::before) {
    color: #ff4d4f !important;
  }

  :deep(.ant-input),
  :deep(.ant-input-number),
  :deep(.ant-select-selector),
  :deep(textarea.ant-input) {
    border-color: #edf0f5 !important;
    border-radius: 8px !important;
    background: #fcf9fc !important;
    box-shadow: none !important;
  }

  :deep(.ant-input),
  :deep(.ant-select-selector) {
    min-height: 38px;
  }

  :deep(textarea.ant-input) {
    resize: none;
  }

  @media (max-width: 680px) {
    .form-grid {
      grid-template-columns: 1fr;
    }
  }
</style>
