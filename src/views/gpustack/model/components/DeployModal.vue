<template>
  <BasicModal
    destroyOnClose
    @register="registerModal"
    :canFullscreen="false"
    width="720px"
    :title="modalTitle"
    :bodyStyle="{ maxHeight: '72vh', overflowY: 'auto' }"
  >
    <div class="deploy-modal">
      <a-form ref="formRef" :model="formState" :rules="rules" layout="vertical">
        <!-- 模型来源 -->
        <section class="section">
          <div class="section-title"><strong>模型来源</strong></div>
          <a-form-item label="来源类型" name="source" required>
            <a-radio-group v-model:value="formState.source" button-style="solid" :disabled="isUpdate">
              <a-radio-button value="huggingface">HuggingFace</a-radio-button>
              <a-radio-button value="model_scope">ModelScope</a-radio-button>
              <a-radio-button value="local_path">本地路径</a-radio-button>
            </a-radio-group>
          </a-form-item>

          <a-form-item
            v-if="formState.source === 'huggingface'"
            label="HuggingFace Repo ID"
            name="huggingface_repo_id"
            :required="formState.source === 'huggingface'"
          >
            <a-input v-model:value="formState.huggingface_repo_id" placeholder="如 Qwen/Qwen2.5-7B-Instruct" />
          </a-form-item>
          <a-form-item v-if="formState.source === 'huggingface'" label="文件名(可选)">
            <a-input v-model:value="formState.huggingface_filename" placeholder="如 *.gguf，留空使用默认" />
          </a-form-item>

          <a-form-item
            v-if="formState.source === 'model_scope'"
            label="ModelScope 模型 ID"
            name="model_scope_model_id"
            :required="formState.source === 'model_scope'"
          >
            <a-input v-model:value="formState.model_scope_model_id" placeholder="如 Qwen/Qwen2.5-7B-Instruct" />
          </a-form-item>
          <a-form-item v-if="formState.source === 'model_scope'" label="文件路径(可选)">
            <a-input v-model:value="formState.model_scope_file_path" placeholder="如 *.gguf" />
          </a-form-item>

          <a-form-item
            v-if="formState.source === 'local_path'"
            label="本地路径"
            name="local_path"
            :required="formState.source === 'local_path'"
          >
            <a-input v-model:value="formState.local_path" placeholder="如 /models/qwen2.5-7b" />
          </a-form-item>

          <a-form-item label="模型名称(推理引用名)" name="name" required>
            <a-input v-model:value="formState.name" placeholder="部署后 OpenAI API 调用所用的模型名" />
          </a-form-item>
        </section>

        <!-- 推理后端 -->
        <section class="section">
          <div class="section-title"><strong>推理后端</strong></div>
          <a-row :gutter="12">
            <a-col :span="12">
              <a-form-item label="后端" name="backend">
                <a-select
                  v-model:value="formState.backend"
                  :options="backendOptions"
                  :loading="optionsLoading"
                  placeholder="留空由 GPUStack 自动选择"
                  allow-clear
                />
              </a-form-item>
            </a-col>
            <a-col :span="12">
              <a-form-item label="后端版本">
                <a-select
                  v-model:value="formState.backend_version"
                  :options="backendVersionOptions"
                  placeholder="选择版本"
                  allow-clear
                />
              </a-form-item>
            </a-col>
          </a-row>
          <a-form-item label="后端启动参数(每行一个，如 --max-model-len=8192)">
            <a-textarea
              v-model:value="backendParamsText"
              :rows="3"
              placeholder="--max-model-len=8192&#10;--gpu-memory-utilization=0.9"
            />
          </a-form-item>
        </section>

        <!-- 调度策略 -->
        <section class="section">
          <div class="section-title"><strong>调度策略</strong></div>
          <a-row :gutter="12">
            <a-col :span="12">
              <a-form-item label="调度策略">
                <a-select
                  v-model:value="formState.placement_strategy"
                  :options="[
                    { label: 'Spread（分散）', value: 'spread' },
                    { label: 'Binpack（聚拢）', value: 'binpack' },
                  ]"
                />
              </a-form-item>
            </a-col>
            <a-col :span="12">
              <a-form-item label="副本数" name="replicas">
                <a-input-number v-model:value="formState.replicas" :min="0" :max="20" style="width: 100%" />
              </a-form-item>
            </a-col>
          </a-row>
          <a-row :gutter="12">
            <a-col :span="12">
              <a-form-item label="每副本 GPU 数" help="多卡/张量并行时填写，留空自动分配">
                <a-input-number
                  v-model:value="gpusPerReplica"
                  :min="1"
                  :max="8"
                  style="width: 100%"
                  placeholder="自动"
                />
              </a-form-item>
            </a-col>
            <a-col :span="12">
              <a-form-item label="跨节点分布式推理">
                <a-switch
                  v-model:checked="formState.distributed_inference_across_workers"
                  checked-children="开"
                  un-checked-children="关"
                />
              </a-form-item>
            </a-col>
          </a-row>
          <a-form-item label="Worker 标签选择器(JSON，可选)">
            <a-textarea
              v-model:value="workerSelectorText"
              :rows="2"
              placeholder='{"worker-name":"cqie"}'
            />
          </a-form-item>
        </section>

        <!-- 运行时选项 -->
        <section class="section last">
          <div class="section-title"><strong>运行时</strong></div>
          <a-row :gutter="12">
            <a-col :span="12">
              <a-form-item label="错误自动重启">
                <a-switch
                  v-model:checked="formState.restart_on_error"
                  checked-children="开"
                  un-checked-children="关"
                />
              </a-form-item>
            </a-col>
            <a-col :span="12">
              <a-form-item label="通用代理模式">
                <a-switch
                  v-model:checked="formState.generic_proxy"
                  checked-children="开"
                  un-checked-children="关"
                />
              </a-form-item>
            </a-col>
          </a-row>
        </section>
      </a-form>
    </div>
    <template #footer>
      <a-button @click="closeModal" size="large">取消</a-button>
      <a-button type="primary" :loading="loading" @click="handleSubmit" size="large">提交</a-button>
    </template>
  </BasicModal>
</template>

<script lang="ts">
  import { computed, defineComponent, reactive, ref } from 'vue';
  import type { FormInstance } from 'ant-design-vue';
  import BasicModal from '@/components/Modal/src/BasicModal.vue';
  import { useModalInner } from '@/components/Modal';
  import { useMessage } from '/@/hooks/web/useMessage';
  import { modelDetail, modelDeploy, modelEdit, modelOptions } from '../../gpustack.api';

  const defaultForm = () => ({
    id: undefined as any,
    source: 'huggingface',
    huggingface_repo_id: '',
    huggingface_filename: '',
    model_scope_model_id: '',
    model_scope_file_path: '',
    local_path: '',
    name: '',
    backend: undefined as any,
    backend_version: undefined as any,
    backend_parameters: [] as string[],
    placement_strategy: 'spread',
    replicas: 1,
    distributed_inference_across_workers: false,
    worker_selector: undefined as any,
    restart_on_error: true,
    generic_proxy: true,
  });

  export default defineComponent({
    name: 'DeployModal',
    components: { BasicModal },
    emits: ['success', 'register'],
    setup(_, { emit }) {
      const { createMessage } = useMessage();
      const loading = ref(false);
      const isUpdate = ref(false);
      const formRef = ref<FormInstance>();
      const formState = reactive<any>(defaultForm());
      const backendParamsText = ref('');
      const workerSelectorText = ref('');
      const gpusPerReplica = ref<number | undefined>(undefined);
      const optionsLoading = ref(false);
      const backendList = ref<any[]>([]);
      const backendVersionOptions = ref<any[]>([]);

      const backendOptions = computed(() =>
        (backendList.value[0]?.items || backendList.value || []).map((b: any) => ({
          label: b.backend_name,
          value: b.backend_name,
        })),
      );

      const rules = computed(() => ({
        source: [{ required: true, message: '请选择来源', trigger: 'change' }],
        name: [{ required: true, message: '请输入模型名称', trigger: 'blur' }],
      }));

      const modalTitle = computed(() => (isUpdate.value ? '编辑模型部署' : '部署新模型'));

      function resetForm(record?: any) {
        Object.assign(formState, defaultForm(), record || {});
        backendParamsText.value = Array.isArray(record?.backend_parameters)
          ? record.backend_parameters.join('\n')
          : '';
        workerSelectorText.value = record?.worker_selector
          ? JSON.stringify(record.worker_selector)
          : '';
        gpusPerReplica.value = record?.gpu_selector?.gpus_per_replica;
      }

      const [registerModal, { closeModal, setModalProps }] = useModalInner(async (data) => {
        setModalProps({ confirmLoading: false });
        // 加载后端选项
        optionsLoading.value = true;
        try {
          const opts = await modelOptions();
          backendList.value = opts?.backends || [];
          // 切换后端时更新版本
          if (formState.backend) updateVersions(formState.backend);
        } catch (e) {
          backendList.value = [];
        } finally {
          optionsLoading.value = false;
        }

        if (data?.id) {
          isUpdate.value = true;
          const res = await modelDetail(data.id);
          resetForm(res || {});
        } else {
          isUpdate.value = false;
          resetForm(data || {});
        }
      });

      function updateVersions(backendName: string) {
        const found = (backendList.value[0]?.items || backendList.value || []).find(
          (b: any) => b.backend_name === backendName,
        );
        backendVersionOptions.value = (found?.versions || []).map((v: any) => ({
          label: v.version,
          value: v.version,
        }));
      }

      function buildPayload() {
        // 后端参数：按行拆分
        const params = backendParamsText.value
          .split('\n')
          .map((s) => s.trim())
          .filter(Boolean);
        // worker_selector JSON
        let workerSelector: any = undefined;
        if (workerSelectorText.value.trim()) {
          try {
            workerSelector = JSON.parse(workerSelectorText.value.trim());
          } catch {
            throw new Error('Worker 标签选择器 JSON 格式错误');
          }
        }
        // gpu_selector
        let gpuSelector: any = undefined;
        if (gpusPerReplica.value) {
          gpuSelector = { gpus_per_replica: gpusPerReplica.value };
        }
        return {
          ...formState,
          backend_parameters: params.length ? params : undefined,
          worker_selector: workerSelector,
          gpu_selector: gpuSelector,
        };
      }

      async function handleSubmit() {
        try {
          loading.value = true;
          setModalProps({ confirmLoading: true });
          await formRef.value?.validate();
          const payload = buildPayload();
          if (isUpdate.value) {
            await modelEdit(payload);
            createMessage.success('更新成功');
          } else {
            await modelDeploy(payload);
            createMessage.success('部署任务已提交');
          }
          closeModal();
          emit('success', { isUpdate: isUpdate.value, values: payload });
        } catch (e: any) {
          if (e?.message) createMessage.error(e.message);
        } finally {
          loading.value = false;
          setModalProps({ confirmLoading: false });
        }
      }

      return {
        registerModal,
        modalTitle,
        formRef,
        formState,
        rules,
        isUpdate,
        loading,
        optionsLoading,
        backendOptions,
        backendVersionOptions,
        backendParamsText,
        workerSelectorText,
        gpusPerReplica,
        closeModal,
        handleSubmit,
      };
    },
  });
</script>

<style scoped lang="less">
  .deploy-modal {
    padding: 16px 4px 8px 0;
  }
  .section {
    padding: 8px 0 16px;
    border-bottom: 1px solid #edf0f5;
  }
  .section.last {
    border-bottom: 0;
  }
  .section-title {
    margin-bottom: 14px;
    strong {
      color: #1f2937;
      font-size: 15px;
    }
  }
</style>
