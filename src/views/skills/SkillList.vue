<template>
  <div class="skill-list">
    <BasicTable @register="registerTable">
      <template #tableTitle>
        <a-space>
          <a-button type="primary" preIcon="ant-design:upload-outlined" @click="uploadOpen = true">上传 Skill</a-button>
        </a-space>
      </template>
      <template #enabledSwitch="{ record }">
        <a-switch
          :checked="record.enabled === 1"
          checked-children="启用"
          un-checked-children="停用"
          @change="(checked) => handleStatusChange(record, checked ? 1 : 0)"
        />
      </template>
      <template #action="{ record }">
        <TableAction :actions="getTableAction(record)" />
      </template>
    </BasicTable>

    <SkillUploadModal v-model:open="uploadOpen" @success="reload" />
    <SkillDetailDrawer v-model:open="detailOpen" :id="currentSkillId" />
  </div>
</template>

<script lang="ts" setup name="skill-list">
  import { ref } from 'vue';
  import { BasicTable, TableAction } from '/@/components/Table';
  import { useListPage } from '/@/hooks/system/useListPage';
  import SkillDetailDrawer from './components/SkillDetailDrawer.vue';
  import SkillUploadModal from './components/SkillUploadModal.vue';
  import { columns, searchFormSchema } from './skill.data';
  import { deleteSkill, disableSkill, enableSkill, list } from './skill.api';

  const uploadOpen = ref(false);
  const detailOpen = ref(false);
  const currentSkillId = ref('');

  const { tableContext } = useListPage({
    tableProps: {
      title: 'Skill 管理',
      api: list,
      columns,
      formConfig: {
        schemas: searchFormSchema,
      },
      actionColumn: {
        width: 180,
      },
    },
  });

  const [registerTable, { reload }] = tableContext;

  function handleDetail(record) {
    currentSkillId.value = record.id;
    detailOpen.value = true;
  }

  async function handleStatusChange(record, enabled) {
    if (enabled === 1) {
      await enableSkill({ id: record.id });
    } else {
      await disableSkill({ id: record.id });
    }
    reload();
  }

  async function handleDelete(record) {
    await deleteSkill({ id: record.id });
    reload();
  }

  function getTableAction(record) {
    return [
      {
        label: '管理',
        onClick: handleDetail.bind(null, record),
      },
      {
        label: '删除',
        color: 'error',
        popConfirm: {
          title: '确定删除吗?',
          confirm: handleDelete.bind(null, record),
        },
      },
    ];
  }
</script>

<style scoped lang="less">
  .skill-list {
    min-height: 100%;
    padding: 24px;
    background: #f0f2f5;
  }
</style>
