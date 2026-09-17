<!-- eslint-disable vue/multi-word-component-names -->
<template>
  <BasicTable :ellipsis="true" @register="registerTable" :searchInfo="searchInfo" :columns="logColumns" :expand-column-width="16">
    <template #tableTitle>
      <div class="table-title-bar">
        <a-tabs defaultActiveKey="4" @change="tabChange" size="small">
          <a-tab-pane tab="异常日志" key="4"></a-tab-pane>
          <a-tab-pane tab="登录日志" key="1"></a-tab-pane>
          <a-tab-pane tab="操作日志" key="2"></a-tab-pane>
          <a-tab-pane tab="日志审计" key="audit"></a-tab-pane>
        </a-tabs>
        <span class="export-btn" v-if="searchInfo.logType == 2">
          <a-tooltip>
            <template #title>导出</template>
            <a-button  type="text" preIcon="ant-design:download-outlined" shape="circle" @click="onExportXls" />
          </a-tooltip>
        </span>
      </div>
    </template>
    <template #expandedRowRender="{ record }">
      <div v-if="searchInfo.logType == 2">
        <div style="margin-bottom: 5px">
          <a-badge status="success" style="vertical-align: middle" />
          <span style="vertical-align: middle">请求方法:{{ record.method }}</span></div
        >
        <div>
          <a-badge status="processing" style="vertical-align: middle" />
          <span style="vertical-align: middle">请求参数:{{ record.requestParam }}</span></div
        >
      </div>
      <div v-if="searchInfo.logType == 4">
        <div style="margin-bottom: 5px">
          <a-badge status="success" style="vertical-align: middle" />
          <span class="error-box" style="vertical-align: middle">异常堆栈:{{ record.requestParam }}</span>
        </div>
      </div>
      <div v-if="searchInfo.logType == 'audit'">
        <div style="margin-bottom: 5px">
          <a-badge status="processing" style="vertical-align: middle" />
          <span style="vertical-align: middle">审计详情:{{ record.detail || '—' }}</span>
        </div>
      </div>
    </template>
  </BasicTable>
</template>
<script lang="ts" name="monitor-log" setup>
  import { ref } from 'vue';
  import { BasicTable } from '/@/components/Table';
  import { getAuditLogList, getLogList, getExportUrl } from './log.api';
  import {
    columns,
    searchFormSchema,
    operationLogColumn,
    operationSearchFormSchema,
    auditColumns,
    auditSearchFormSchema,
    exceptionColumns
  } from './log.data';
  import { useListPage } from '/@/hooks/system/useListPage';

  const logColumns = ref<any>(exceptionColumns);
  const searchSchema = ref<any>(searchFormSchema);
  const searchInfo = { logType: '4' };
  // 列表页面公共参数、方法
  const { tableContext, onExportXls } = useListPage({
    designScope: 'user-list',
    tableProps: {
      title: '日志列表',
      api: getLogList,
      expandRowByClick: true,
      showActionColumn: false,
      rowSelection: {
        columnWidth: 20,
      },
      formConfig: {
        schemas: searchSchema,
        fieldMapToTime: [['fieldTime', ['createTime_begin', 'createTime_end'], 'YYYY-MM-DD']],
      },
    },
    exportConfig: {
      name:"操作日志",
      url: getExportUrl,
      params: searchInfo,
      timeout: 300000, // 设置超时时间为5分钟(300秒)
    },
  });

  const [registerTable, { reload, setProps }] = tableContext;

  // 日志类型
  function tabChange(key) {
    searchInfo.logType = key;
    // 代码逻辑说明: [VUEN-943]vue3日志管理列表翻译不对------------
    if (key === 'audit') {
      logColumns.value = auditColumns;
      searchSchema.value = auditSearchFormSchema;
      setProps({
        api: getAuditLogList,
        columns: auditColumns,
        formConfig: {
          schemas: auditSearchFormSchema,
          fieldMapToTime: [['fieldTime', ['createTime_begin', 'createTime_end'], 'YYYY-MM-DD']],
        },
      });
    } else if (key == '2') {
      logColumns.value = operationLogColumn;
      searchSchema.value = operationSearchFormSchema;
      setProps({
        api: getLogList,
        columns: operationLogColumn,
        formConfig: {
          schemas: operationSearchFormSchema,
          fieldMapToTime: [['fieldTime', ['createTime_begin', 'createTime_end'], 'YYYY-MM-DD']],
        },
      });
    }else if(key == '4'){
      searchSchema.value = searchFormSchema;
      logColumns.value = exceptionColumns;
      setProps({
        api: getLogList,
        columns: exceptionColumns,
        formConfig: {
          schemas: searchFormSchema,
          fieldMapToTime: [['fieldTime', ['createTime_begin', 'createTime_end'], 'YYYY-MM-DD']],
        },
      });
    } else {
      searchSchema.value = searchFormSchema;
      logColumns.value = columns;
      setProps({
        api: getLogList,
        columns,
        formConfig: {
          schemas: searchFormSchema,
          fieldMapToTime: [['fieldTime', ['createTime_begin', 'createTime_end'], 'YYYY-MM-DD']],
        },
      });
    }
    reload({ page: 1 });
  }
</script>
<style lang="less" scoped>
  .error-box {
    white-space: break-spaces;
  }
  .table-title-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    width: 100%;
  }
  .export-btn {
    margin-left: auto;
  }
  :deep(.jeecg-basic-table-header__toolbar){
    width:100px !important;
  }
</style>
