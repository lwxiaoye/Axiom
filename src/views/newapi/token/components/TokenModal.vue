<template>
  <BasicModal
    destroyOnClose
    @register="registerModal"
    :canFullscreen="false"
    width="620px"
    :title="modalTitle"
    :centered="true"
    wrapClassName="token-config-modal"
  >
    <div class="token-modal">
      <a-form ref="formRef" :model="formState" :rules="rules" layout="vertical">
        <section class="token-section">
          <div class="section-title">
            <span class="section-icon basic">🔑</span>
            <div>
              <strong>基本信息</strong>
              <p>设置令牌的基础信息</p>
            </div>
          </div>

          <a-form-item label="名称" name="name" required>
            <a-input v-model:value="formState.name" placeholder="请输入令牌名称" />
          </a-form-item>

          <div class="expire-row">
            <a-form-item label="过期时间" name="expired_time" required class="expire-picker">
              <a-date-picker
                v-model:value="expireDate"
                :disabled="formState.expired_time === -1"
                format="YYYY-MM-DD HH:mm:ss"
                show-time
                placeholder="请选择过期时间"
                style="width: 100%"
                @change="syncExpireTime"
              />
            </a-form-item>
            <div class="quick-expire">
              <span>过期时间快捷设置</span>
              <a-space wrap>
                <a-button size="small" :type="formState.expired_time === -1 ? 'primary' : 'default'" @click="setExpireForever">永不过期</a-button>
                <a-button size="small" @click="setExpireAfter('month')">一个月</a-button>
                <a-button size="small" @click="setExpireAfter('day')">一天</a-button>
                <a-button size="small" @click="setExpireAfter('hour')">一小时</a-button>
              </a-space>
            </div>
          </div>
        </section>

        <section class="token-section">
          <div class="section-title">
            <span class="section-icon quota">💳</span>
            <div>
              <strong>额度设置</strong>
              <p>设置令牌可用额度和数量</p>
            </div>
          </div>

          <a-form-item label="金额" name="remain_amount">
            <a-input-number
              v-model:value="formState.remain_amount"
              :min="0"
              :precision="6"
              :disabled="formState.unlimited_quota"
              addon-before="¥"
              placeholder="0.000000"
              style="width: 100%"
            />
            <p class="field-tip">使用现金额度输入</p>
          </a-form-item>

          <a-form-item v-if="!formState.unlimited_quota" label="剩余额度" name="remain_quota">
            <a-input-number v-model:value="formState.remain_quota" :min="0" placeholder="请输入剩余额度" style="width: 100%" />
          </a-form-item>

          <a-form-item label="无限额度" name="unlimited_quota">
            <a-switch v-model:checked="formState.unlimited_quota" />
            <p class="field-tip">令牌额度仅用于限制令牌本身的最大额度使用量，实际的使用受到账号剩余额度限制。</p>
          </a-form-item>

        </section>

        <section class="token-section">
          <div class="section-title">
            <span class="section-icon access">🔗</span>
            <div>
              <strong>访问限制</strong>
              <p>设置令牌访问限制</p>
            </div>
          </div>

          <a-form-item label="启用模型限制" name="model_limits_enabled">
            <a-switch v-model:checked="formState.model_limits_enabled" />
          </a-form-item>

          <a-form-item label="模型限制列表" name="model_limits">
            <a-select
              v-model:value="selectedModels"
              mode="multiple"
              show-search
              allow-clear
              :disabled="!formState.model_limits_enabled"
              :loading="modelLoading"
              :options="modelOptions"
              placeholder="请选择该令牌支持的模型，留空支持所有模型"
            />
            <p class="field-tip">非必要，不建议启用模型限制</p>
          </a-form-item>

          <a-form-item label="IP白名单（支持CIDR表达式）" name="allow_ips">
            <a-textarea v-model:value="formState.allow_ips" placeholder="请输入 IP 白名单，一行一个" :rows="4" />
            <p class="field-tip">请勿过度信任此功能，IP可能被伪造，请配合 nginx 和 cdn 等网关使用。</p>
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
  import dayjs, { Dayjs } from 'dayjs';
  import type { FormInstance } from 'ant-design-vue';
  import BasicModal from '@/components/Modal/src/BasicModal.vue';
  import { useModalInner } from '@/components/Modal';
  import { create, update, getById, listCanUseModel } from '../token.api';
  import { useMessage } from '/@/hooks/web/useMessage';
  import { CheckOutlined } from '@ant-design/icons-vue';

  const defaultTokenForm = () => ({
    id: undefined,
    name: '',
    group: 'default',
    remain_quota: 0,
    remain_amount: 0,
    expired_time: -1,
    unlimited_quota: true,
    model_limits_enabled: false,
    model_limits: '',
    cross_group_retry: false,
    allow_ips: '',
  });

  export default defineComponent({
    name: 'TokenModal',
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
      const formState = reactive<any>(defaultTokenForm());
      const expireDate = ref<Dayjs | null>(null);
      const modelOptions = ref<any[]>([]);
      const selectedModels = ref<string[]>([]);
      const modelLoading = ref(false);

      const rules = {
        name: [{ required: true, message: '请输入令牌名称', trigger: 'blur' }],
      };

      const modalTitle = computed(() => (isUpdate.value ? '更新令牌信息' : '新增令牌'));

      const loadModels = async () => {
        modelLoading.value = true;
        try {
          const res = await listCanUseModel();
          modelOptions.value = Array.isArray(res)
            ? res.map((model) => ({
                label: model,
                value: model,
              }))
            : [];
        } catch (error) {
          console.error('获取模型列表失败:', error);
          modelOptions.value = [];
        } finally {
          modelLoading.value = false;
        }
      };

      function toBoolean(value: any) {
        return value === true || value === 1;
      }

      function resetForm(record?: Recordable) {
        Object.assign(formState, defaultTokenForm(), record || {});
        formState.group = formState.group || 'default';
        formState.remain_quota = Number(formState.remain_quota || 0);
        formState.remain_amount = Number(formState.remain_amount || 0);
        formState.unlimited_quota = toBoolean(formState.unlimited_quota);
        formState.model_limits_enabled = toBoolean(formState.model_limits_enabled);
        formState.cross_group_retry = toBoolean(formState.cross_group_retry);

        selectedModels.value = formState.model_limits
          ? String(formState.model_limits)
              .split(',')
              .map((item) => item.trim())
              .filter(Boolean)
          : [];

        if (formState.expired_time && formState.expired_time !== -1) {
          expireDate.value = dayjs(Number(formState.expired_time) * 1000);
        } else {
          formState.expired_time = -1;
          expireDate.value = null;
        }
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

      function syncExpireTime(value?: Dayjs | null) {
        if (value) {
          formState.expired_time = value.unix();
        }
      }

      function setExpireForever() {
        formState.expired_time = -1;
        expireDate.value = null;
      }

      function setExpireAfter(type: 'month' | 'day' | 'hour') {
        const unitMap = {
          month: 'month',
          day: 'day',
          hour: 'hour',
        } as const;
        expireDate.value = dayjs().add(1, unitMap[type]);
        syncExpireTime(expireDate.value);
      }

      function buildPayload() {
        const payload = {
          ...formState,
          model_limits: formState.model_limits_enabled ? selectedModels.value.join(',') : '',
          expired_time: expireDate.value ? expireDate.value.unix() : -1,
        };
        return payload;
      }

      async function handleSubmit() {
        try {
          loading.value = true;
          setModalProps({ confirmLoading: true });
          await formRef.value?.validate();
          const values = buildPayload();
          if (isUpdate.value) {
            await update(values);
            createMessage.success('更新成功');
          } else {
            await create(values);
            createMessage.success('新增成功');
          }
          closeModal();
          emit('success', { isUpdate: isUpdate.value, values });
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
        expireDate,
        selectedModels,
        modelOptions,
        modelLoading,
        loading,
        syncExpireTime,
        setExpireForever,
        setExpireAfter,
        handleSubmit,
        handleCancel,
      };
    },
  });
</script>

<style lang="less">
  .token-config-modal {
    .ant-modal {
      max-width: calc(100vw - 32px);
    }

    .ant-modal-content {
      display: flex;
      flex-direction: column;
      max-height: calc(100vh - 48px);
      border-radius: 8px;
      overflow: hidden;
      box-shadow: 0 16px 42px rgba(15, 23, 42, 0.18);
    }

    .ant-modal-header {
      flex: 0 0 auto;
      padding: 18px 24px;
      border-bottom: 1px solid #edf0f5;
    }

    .ant-modal-title {
      color: #111827;
      font-size: 18px;
      font-weight: 800;
    }

    .ant-modal-close {
      top: 13px;
    }

    .ant-modal-body {
      flex: 1 1 auto;
      min-height: 0;
      padding: 0 16px 0 24px;
      overflow: auto;
      background: #fff;
    }

    .ant-modal-footer {
      flex: 0 0 auto;
      padding: 16px 24px;
      border-top: 1px solid #edf0f5;
      background: #fff;

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
  .token-modal {
    padding: 16px 6px 24px 0;
  }

  .token-section {
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
    border-radius: 50%;
    font-size: 15px;

    &.basic {
      background: #e8f2ff;
    }

    &.quota {
      background: #eaf8ee;
    }

    &.access {
      background: #f4eaff;
    }
  }

  .expire-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 270px;
    gap: 18px;
    align-items: end;
  }

  .quick-expire {
    padding-bottom: 24px;

    > span {
      display: block;
      margin-bottom: 8px;
      color: #1f2937;
      font-size: 13px;
      font-weight: 600;
    }
  }

  .field-tip {
    margin: 8px 0 0;
    color: #64748b;
    font-size: 12px;
    line-height: 1.6;
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
  :deep(.ant-picker),
  :deep(textarea.ant-input) {
    border-color: #edf0f5 !important;
    border-radius: 8px !important;
    background: #fcf9fc !important;
    box-shadow: none !important;
  }

  :deep(.ant-input),
  :deep(.ant-select-selector),
  :deep(.ant-picker) {
    min-height: 38px;
  }

  :deep(textarea.ant-input) {
    resize: none;
  }

  @media (max-width: 680px) {
    .expire-row {
      grid-template-columns: 1fr;
    }

    .quick-expire {
      padding-bottom: 0;
    }
  }
</style>
