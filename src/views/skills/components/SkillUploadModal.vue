<template>
  <a-modal :open="open" title="上传 Skill" :confirm-loading="loading" :body-style="{ padding: '20px 24px' }" @cancel="handleClose" @ok="handleUpload">
    <a-alert
      class="mb-3"
      type="info"
      show-icon
      :message="`请上传包含 SKILL.md 的 zip 包，大小不超过 ${SKILL_UPLOAD_MAX_SIZE_LABEL}，系统会解析元数据并安装到本地 Skill 目录。`"
    />
    <a-checkbox v-model:checked="overwrite" class="mb-3">存在同名 Skill 时覆盖安装</a-checkbox>
    <a-upload-dragger
      v-model:file-list="fileList"
      accept=".zip"
      :max-count="1"
      :before-upload="beforeUpload"
    >
      <p class="ant-upload-drag-icon">
        <InboxOutlined />
      </p>
      <p class="ant-upload-text">点击或拖拽 zip 文件到此处上传</p>
    </a-upload-dragger>
  </a-modal>
</template>

<script lang="ts" setup>
  import { InboxOutlined } from '@ant-design/icons-vue';
  import { LIST_IGNORE } from 'ant-design-vue/es/upload/Upload';
  import { ref } from 'vue';
  import { useMessage } from '/@/hooks/web/useMessage';
  import { uploadSkill } from '../skill.api';
  import { isSkillUploadFileAllowed, SKILL_UPLOAD_MAX_SIZE_LABEL } from '../skillUpload';

  defineProps({
    open: { type: Boolean, default: false },
  });

  const emit = defineEmits(['update:open', 'success']);
  const { createMessage } = useMessage();
  const loading = ref(false);
  const overwrite = ref(false);
  const fileList = ref<any[]>([]);

  function beforeUpload(file) {
    if (!isSkillUploadFileAllowed(file)) {
      createMessage.warning(`请上传不超过 ${SKILL_UPLOAD_MAX_SIZE_LABEL} 的 Skill zip 包`);
      return LIST_IGNORE;
    }
    fileList.value = [file];
    return false;
  }

  function handleClose() {
    emit('update:open', false);
  }

  async function handleUpload() {
    const file = fileList.value[0]?.originFileObj || fileList.value[0];
    if (!file) {
      createMessage.warning('请选择 Skill zip 包');
      return;
    }
    if (!isSkillUploadFileAllowed(file)) {
      createMessage.warning(`请上传不超过 ${SKILL_UPLOAD_MAX_SIZE_LABEL} 的 Skill zip 包`);
      return;
    }
    loading.value = true;
    try {
      await uploadSkill({
        file,
        data: {
          overwrite: overwrite.value,
        },
      });
      fileList.value = [];
      emit('success');
      handleClose();
    } finally {
      loading.value = false;
    }
  }
</script>

<style scoped>
  .mb-3 {
    margin-bottom: 12px;
  }
</style>
