<template>
  <div style="box-sizing: border-box; height: 100%; position: relative">
    <ScrollContainer ref="wrapperRef">
      <a-timeline style="padding: 12px">
        <a-timeline-item v-for="(item, index) of state.auditLogs" :color="item.color">
          <template #dot>
            <ClockCircleOutlined v-if="!item.endTime" />
            <CloseCircleOutlined v-else-if="item.processResult && (item.processResult === 'reject' || item.processResult === 'closed')" />
            <UndoOutlined v-else-if="item.processResult && item.processResult === 'revoke'" />
            <CheckCircleOutlined v-else />
          </template>

          <div class="flowTitle"
            >{{
              item.processResult === 'closed'
                ? '已关闭'
                : item.processResult === 'end'
                  ? '已通过'
                  : item.processResult === 'revoke'
                    ? '撤销申请'
                    : item.activityName
            }}
            <!--            <span v-if="item.processResult == 'revoke'" style="color: gray">（撤销）</span>-->
            <span v-if="item.processResult == 'passed'" style="color: green">（通过）</span>
            <span v-if="item.processResult == 'reject'" style="color: red">（驳回）</span>
            <!--            <span v-if="item.processResult == 'closed'" style="color: gray">（关闭）</span>-->
          </div>
          <div class="flowTime">
            <span style="margin-right: 4px">{{ item.assignee_dictText }}</span
            ><span v-if="item.endTime">{{ item.endTime }}</span>
          </div>
          <div class="flowContent" v-if="item.comments && item.comments.length > 0">
            {{ item.comments[0] ? item.comments[0].fullMessage : '--' }}</div
          >
        </a-timeline-item>
      </a-timeline>
    </ScrollContainer>
  </div>
</template>
<script lang="ts" setup>
  import { CheckCircleOutlined, CloseCircleOutlined, ClockCircleOutlined, UndoOutlined } from '@ant-design/icons-vue';
  import { queryTransferRecords } from '../FlowData.api';
  import { onMounted, reactive, watch } from 'vue';
  import { ScrollContainer } from '@/components/Container';
  let props = defineProps<{
    indexKey?: string;
  }>();
  let state = reactive<any>({
    auditLogs: [],
    activeName: 'time',
  });
  const init = (processInstanceId) => {
    state.auditLogs = [];
    queryTransferRecords(processInstanceId).then((res) => {
      if (res) {
        let lastData = res[res.length - 1];
        res.forEach((item: any) => {
          if (!item.endTime) {
            item.color = 'blue';
          } else if (item.processResult === 'closed' || item.processResult == 'revoke') {
            item.color = 'gray';
          } else if (item.processResult === 'reject') {
            item.color = 'red';
          } else {
            item.color = 'green';
          }
        });
        if (lastData.processResult === 'closed') {
          // res.push({
          //   isStartNode: false,
          //   processResult: 'end',
          //   endTime: lastData.endTime,
          //   color: 'gray',
          // });
        }
      }
      state.auditLogs = res;
    });
  };
  defineExpose({
    init,
  });
</script>
<style scoped lang="less">
  .flowTitle {
    font-size: 14px;
  }

  .flowTime {
    color: gray;
    font-size: 12px;
    font-weight: 400;
  }

  .flowContent {
    color: gray;
    font-size: 12px;
    font-weight: 400;
  }
</style>
