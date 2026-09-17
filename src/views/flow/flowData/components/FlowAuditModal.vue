<template>
  <BasicModal
    v-bind="$attrs"
    @register="registerModal"
    :title="state.title"
    :showOkBtn="type != 'detail'"
    :showCancelBtn="type != 'detail'"
    okText="提交"
    @ok="onSubmit"
    :width="1200"
    :maxHeight="700"
    centered
  >
    <div :style="`height: calc(100% - ${auditFormHeight}px); width: calc(100%); padding: 0 12px`">
      <div style="display: flex; height: 100%" data-content-ref="true">
        <ScrollContainer ref="wrapperRef">
          <form-create
            v-show="activeKey == 'basicInfo' && types == 'other'"
            :disabled="true"
            ref="applyFormRef"
            v-model="formData"
            :rule="rule"
            :option="option"
          />
          <div v-if="types == 'program'">
            <div class="infoBox bb-1">
              <div class="number mb-6">审批编号：{{ pageInfo.id }}</div>
              <div class="title mb-2">{{ pageInfo.createBy_dictText }}发起的人才培养方案审批</div>
              <!-- <div class="number">{{ pageInfo.dwh_dictText }}（{{ pageInfo.zyh_dictText }}）</div> -->
            </div>
            <div class="infoBox secTitle">
              <div class="mb-4">
                <span>学院：</span>
                <span class="number">{{ pageInfo.dwh_dictText }}</span>
              </div>
              <div class="mb-4">
                <span>专业：</span>
                <span class="number">{{ pageInfo.zyh_dictText }}</span>
              </div>
              <div>
                <div class="mb-4">附件：</div>
                <div class="fileBox flex justify-between mb-4" v-for="it of pageInfo.trainingProgramList" :key="it.filePath">
                  <div class="flex">
                    <img class="icon_32 mr-4" :src="checkType(it.fileName)" />
                    <div class="fileName less">{{ it.fileName }}</div>
                  </div>
                  <div class="downText" @click="downFile(it)"> 下载 </div>
                </div>
              </div>
            </div>
          </div>
          <div v-if="types == 'teacher'">
            <div class="infoBox bb-1">
              <div class="number mb-6">审批编号：{{ pageInfo.processInfoModel.bizId }}</div>
              <div class="title mb-2">{{ pageInfo.processInfoModel.createId_dictText }}发起的教学要件审批</div>
              <!-- <div class="number">{{ pageInfo.dwh_dictText }}（{{ pageInfo.zyh_dictText }}）</div> -->
            </div>
            <div class="infoBox secTitle bb-1">
              <div class="mb-4">
                <span>授课学期：</span>
                <span class="number">{{ pageInfo.xn }}学年第{{ pageInfo.xq }}学期</span>
              </div>
              <div class="mb-4">
                <span>课程名称：</span>
                <span class="number">{{ pageInfo.kch_dictText }}</span>
              </div>
              <div class="mb-4">
                <span>归属专业：</span>
                <span class="number">{{ pageInfo.zyh_dictText }}</span>
              </div>
              <div class="mb-4">
                <span>学院：</span>
                <span class="number">{{ pageInfo.dwh_dictText }}</span>
              </div>
            </div>
            <div class="infoBox mb-4">
              <div class="mb-4">教学大纲：</div>
              <div class="fileBox flex justify-between mb-4" v-for="it of pageInfo.syllabusList" :key="it.filePath">
                <div class="flex">
                  <img class="icon_32 mr-4" :src="checkType(it.fileName)" />
                  <div class="fileName less">{{ it.fileName }}</div>
                </div>
                <div class="downText" @click="downFile(it)"> 下载 </div>
              </div>
              <div class="mb-4">教学计划：</div>
              <div class="fileBox flex justify-between mb-4" v-for="it of pageInfo.planList" :key="it.filePath">
                <div class="flex">
                  <img class="icon_32 mr-4" :src="checkType(it.fileName)" />
                  <div class="fileName less">{{ it.fileName }}</div>
                </div>
                <div class="downText" @click="downFile(it)"> 下载 </div>
              </div>
              <div class="mb-4">教学方案：</div>
              <div class="fileBox flex justify-between mb-4" v-for="it of pageInfo.schemeList" :key="it.filePath">
                <div class="flex">
                  <img class="icon_32 mr-4" :src="checkType(it.fileName)" />
                  <div class="fileName less">{{ it.fileName }}</div>
                </div>
                <div class="downText" @click="downFile(it)"> 下载 </div>
              </div>
            </div>
          </div>
        </ScrollContainer>
        <div class="br-1"></div>
        <FlowLog ref="flowLogRef" class="flowContent" />
      </div>
    </div>
    <div v-if="type != 'detail'" class="auditForm" :style="`height:${auditFormHeight}px`">
      <BasicForm @register="registerForm" @change-fn="changeFn" ref="formRef" name="applyDeptForm" @submit="submit1" />
    </div>

    <a-modal :open="previewVisible" :footer="null" @cancel="handleCancel()">
      <img alt="example" style="width: 100%" :src="previewImage" />
    </a-modal>

    <a-modal v-model:open="modelInfo.showCopy" :title="modelInfo.title" centered :maskClosable="false" :footer="null">
      <div class="modelBox">
        <div class="step1 mb-4">
          <div class="flex">
            <a-input v-model:value="modelInfo.url" class="mr-4" />
            <a-button type="primary" class="copysBtn" :data-clipboard-text="modelInfo.url">复制链接</a-button>
          </div>
        </div>
      </div>
    </a-modal>

    <template #insertFooter v-if="!props.hideBtn">
      <a-button type="primary" v-if="type == 'detail'" :loading="loading" @click="downImg()">下载审批单</a-button>
      <a-button type="primary" v-if="type == 'detail'" @click="share()">分享</a-button>
    </template>
  </BasicModal>
</template>
<script lang="ts" setup>
  import { BasicModal, useModalInner } from '@/components/Modal';
  import { onMounted, onUnmounted } from 'vue';
  import FlowLog from '@/views/flow/flowData/components/FlowLog.vue';
  import { reactive, ref } from 'vue';
  import formCreate from '@form-create/ant-design-vue';
  import { BasicForm, useForm } from '@/components/Form';
  import { pass, reject, close, revoke, rejectNodeList, queryById, queryByProcessInstanceId } from '/@/views/flow/flowData/FlowData.api';
  // import BpmnView from '@/views/flow/flowData/components/BpmnView.vue';
  import { ScrollContainer } from '@/components/Container';
  import { message } from 'ant-design-vue';
  import { downloadByUrl } from '@/utils/file/download';
  import html2canvas from 'html2canvas';
  import ClipboardJS from 'clipboard';

  // import { getAllRolesListNoByTenant } from '@/views/system/user/user.api';
  const emit = defineEmits(['success', 'register']);
  const props = defineProps({
    hideBtn: {
      type: Boolean,
      default: false,
    },
  });
  let state = reactive<any>({
    options: [],
    auditLogs: [],
    title: '',
  });
  let types = ref<any>('other'); // 人才培养 program   教学要件 teacher   其他  other
  //预览框状态
  const previewVisible = ref<boolean>(false); //预览图
  const previewImage = ref<string | undefined>('');

  const flowLogRef = ref<any>(null);
  const activeKey = ref('basicInfo');
  const type = ref('');
  const auditFormHeight = ref(147);
  const [registerForm, { resetFields, updateSchema, setFieldsValue, validate, getFieldsValue }] = useForm({
    labelWidth: 100,
    schemas: [
      {
        label: '审批结果',
        field: 'result',
        required: true,
        component: 'RadioGroup',
        componentProps: {
          options: [
            { label: '通过', value: 'pass' },
            { label: '驳回', value: 'reject' },
          ],
        },
        ifShow: () => {
          return type.value === 'audit';
        },
      },
      {
        label: '驳回节点',
        field: 'rejectTaskDefinitionKey',
        required: true,
        component: 'Select',
        componentProps: {
          options: [],
        },
        colProps: { span: 12 },
        ifShow: ({ values }) => {
          return type.value === 'audit' && values.result === 'reject';
        },
      },
      {
        label: '审批意见',
        field: 'comment',
        required: true,
        component: 'InputTextArea',
        componentProps: {
          maxLength: 800,
        },
      },
    ],
    showActionButtonGroup: false,
    baseColProps: { span: 24 },
  });
  //表单赋值
  const [registerModal] = useModalInner(async (data: any) => {
    console.log(data, 'data');
    type.value = data.type;
    resetFields();
    if (type.value === 'detail') {
      auditFormHeight.value = 0;
    } else if (type.value === 'audit') {
      auditFormHeight.value = 147;
      const list = await rejectNodeList(data.record.processInstanceId);

      rejectNodeMap.value = new Map();
      list.forEach((item) => {
        rejectNodeMap.value.set(item.taskDefinitionKey, item.taskName);
      });
      updateSchema({
        label: '驳回节点',
        field: 'rejectTaskDefinitionKey',
        required: true,
        component: 'Select',
        componentProps: {
          options: list.map((item) => {
            return { value: item.taskDefinitionKey, label: item.taskName };
          }),
        },
        colProps: { span: 12 },
        ifShow: ({ values }) => {
          return type.value === 'audit' && values.result === 'reject';
        },
      });
      setFieldsValue({
        rejectTaskDefinitionKey: list[0].taskDefinitionKey,
      });
    } else {
      auditFormHeight.value = 91;
    }
    updateSchema({
      label: type.value === 'audit' ? '审批意见' : type.value === 'close' ? '关闭说明' : '撤回说明',
      field: 'comment',
      required: true,
      component: 'InputTextArea',
      componentProps: {
        maxLength: 800,
      },
    });
    if (data.record.processDefinitionKey == 'Flow_training_program') {
      types.value = 'program';
      // pageInfo.value = data.record;
      queryByProcessInstanceId({ processInstanceId: data.record.processInstanceId }).then((res) => {
        console.log(res, 555);
        res.trainingProgramList = JSON.parse(res.trainingProgram);
        pageInfo.value = res;
      });
    } else {
      types.value = 'other';
      queryById(data.record.id || data.record.bizId).then((res) => {
        pageInfo.value = res;
        formData.value = formCreate.parseJson(res.dataValue);
        option.value = {
          submitBtn: false, // 不显示默认提交按钮
          form: {
            labelPosition: 'right',
            labelWidth: '150px',
          },
        };
        // option.value = JSON.parse(res.processInfoModel.formOptions);
        rule.value = formCreate.parseJson(res.rule);
        option.value = formCreate.parseJson(res.formOptions);
        rule.value.forEach((item: any) => {
          if (item.type === 'upload') {
            item.on = {
              // 预览事件（关键）
              preview: (file) => {
                let hz = file.url.slice(file.url.lastIndexOf('.') + 1);
                let imageTypes = ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'svg'];
                if (imageTypes.indexOf(hz) > -1) {
                  previewVisible.value = true;
                  previewImage.value = file.file.url;
                } else {
                  downloadByUrl({ url: file.file.url, fileName: file.file.fileName });
                }
              },
            };
          }
        });
      });
    }
    console.log(data.record, 666);
    state.title = data.record.createId_dictText + '-' + (data.record.processDefinitionName || data.record.processInfoModel.processDefinitionName);
    record.value = data.record;
    flowLogRef.value.init(data.record.processInstanceId);
  });

  function handleCancel() {
    previewVisible.value = false;
  }

  const update = () => {
    type.value = 'detail';
    auditFormHeight.value = 0;
    flowLogRef.value.init(record.value.processInstanceId);
  };

  const record: any = ref({});
  const option = ref<any>({});
  const rule = ref<any>([]);
  const formData = ref({});
  const pageInfo = ref<any>({
    processInfoModel: {},
  });
  const rejectNodeMap = ref();
  const changeFn = (schema: any) => {
    let field = schema._value.field;
    if (field === 'result') {
      let result = getFieldsValue().result;
      if (result === 'pass') {
        setFieldsValue({ comment: '通过' });
        auditFormHeight.value = 147;
      }
      if (result === 'reject') {
        setFieldsValue({ comment: '拒绝' });
        auditFormHeight.value = 202;
      }
    }
  };
  let check = false;
  const onSubmit = async () => {
    let value: any = {};
    try {
      value = await validate();
    } catch (e) {
      console.log('e', e);
      message.warning('表单校验失败');
      return;
    }
    if (check) {
      return;
    }
    check = true;

    setTimeout(() => {
      check = false;
    }, 1000);
    // let value = getFieldsValue();
    let api: any = null;
    let data: any = {
      bizId: record.value.bizId || pageInfo.value.processInfoModel.bizId,
      taskId: record.value.taskId,
      processInstanceId: record.value.processInstanceId,
      comment: value.comment,
      id: record.value.id,
    };
    if (type.value === 'close') {
      api = close;
    } else if (type.value === 'revoke') {
      api = revoke;
    } else if (value.result === 'pass') {
      api = pass;
    } else {
      data.rejectTaskDefinitionKey = value.rejectTaskDefinitionKey;
      data.rejectTaskName = rejectNodeMap.value.get(value.rejectTaskDefinitionKey);
      api = reject;
    }
    // console.log('data', data);
    await api(data);
    emit('success');
    update();
  };

  const submit1 = (e) => {
    console.log('submit', e);
  };

  import img from '@/assets/img/img.png';
  import p from '@/assets/img/p.png';
  import pp from '@/assets/img/pp.png';
  import v from '@/assets/img/v.png';
  import w from '@/assets/img/w.png';
  import x from '@/assets/img/x.png';
  import z from '@/assets/img/z.png';
  import o from '@/assets/img/o.png';
  // import { message } from 'ant-design-vue';

  // 判断文件类型并返回对应图标路径
  function checkType(suffix: string) {
    if (!suffix) return o;
    // suffix = suffix.split('.')[1];

    const suffixLower = suffix.split('.')[suffix.split('.').length - 1].toLowerCase();
    console.log('suffixLower', suffixLower);
    // 图片类型
    const imageTypes = ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'svg'];
    if (imageTypes.includes(suffixLower)) {
      return img;
    }

    // 文档类型
    const doc = ['doc', 'docx'];
    if (doc.includes(suffixLower)) {
      return w;
    }

    // 文档类型
    const xlsx = ['xls', 'xlsx'];
    if (xlsx.includes(suffixLower)) {
      return x;
    }

    // 文档类型
    const ppt = ['ppt', 'pptx'];
    if (ppt.includes(suffixLower)) {
      return pp;
    }

    // 文档类型
    const pdf = ['pdf'];
    if (pdf.includes(suffixLower)) {
      return p;
    }

    // 压缩包类型
    const archiveTypes = ['zip', 'rar', '7z', 'tar', 'gz'];
    if (archiveTypes.includes(suffixLower)) {
      return z;
    }

    // 视频类型
    const videoTypes = ['mp4', 'avi', 'mov', 'wmv', 'flv', 'mkv', 'mp3', 'wav', 'ogg', 'aac', 'flac'];
    if (videoTypes.includes(suffixLower)) {
      return v;
    }

    // 默认文件图标
    return o;
  }
  // 文件下载
  function downFile(row: any) {
    let path = '/' + row.filePath;
    downloadByUrl({ url: path, fileName: row.fileName });
  }
  let loading = ref(false);
  // 使用异步处理实现html2canvas截图，避免页面卡死
  function downImg() {
    try {
      loading.value = true;
      // 获取要保存的div元素
      const contentDiv: any = document.querySelector('[data-content-ref="true"]');
      if (!contentDiv) {
        message.warning('未找到要下载的内容');
        loading.value = false;
        return;
      }

      // 保存原始样式，以便后续恢复
      const originalStyle = {
        height: contentDiv.style.height,
        overflow: contentDiv.style.overflow,
        position: contentDiv.style.position,
      };

      // 临时修改样式，确保所有内容可见
      contentDiv.style.height = 'auto';
      contentDiv.style.overflow = 'visible';
      contentDiv.style.position = 'relative';

      // 强制重排
      contentDiv.offsetHeight;

      // 使用setTimeout将截图操作放到下一个事件循环，避免阻塞主线程
      setTimeout(() => {
        try {
          // 使用html2canvas在主线程处理
          const options = {
            scale: 2,
            useCORS: true,
            logging: false,
            backgroundColor: '#ffffff',
            windowWidth: contentDiv.scrollWidth,
            windowHeight: contentDiv.scrollHeight,
            imageTimeout: 10000,
            allowTaint: true,
            async: true, // 启用异步处理
          };

          html2canvas(contentDiv as HTMLElement, options)
            .then((canvas) => {
              // 恢复原始样式
              Object.assign(contentDiv.style, originalStyle);
              // 将Canvas转换为图片
              const dataURL = canvas.toDataURL('image/png');

              // 创建下载链接
              const link = document.createElement('a');
              link.download = '审批表单_' + Date.now() + '.png';
              link.href = dataURL;
              link.click();
              loading.value = false;
              message.success('表单已成功下载为图片');
            })
            .catch((error) => {
              console.error('html2canvas转换失败:', error);
              // 恢复原始样式
              Object.assign(contentDiv.style, originalStyle);
              loading.value = false;
              message.error('转换失败，请重试');
              // 降级处理
              fallbackDownImg();
            });
        } catch (error) {
          console.error('截图处理失败:', error);
          // 恢复原始样式
          Object.assign(contentDiv.style, originalStyle);
          loading.value = false;
          message.error('处理失败，请重试');
          // 降级处理
          fallbackDownImg();
        }
      }, 100); // 延迟100ms执行，避免阻塞主线程
    } catch (error) {
      console.error('初始化失败:', error);
      loading.value = false;
      message.error('初始化失败，请重试');
      // 降级处理
      fallbackDownImg();
    }
  }
  let modelInfo = reactive({
    showCopy: false,
    title: '分享链接',
    url: '',
  });

  // clipboard实例
  let clipboard: any = null;

  // 在组件挂载后初始化clipboard
  onMounted(() => {
    clipboard = new ClipboardJS('.copysBtn');

    clipboard.on('success', (e) => {
      message.success('链接复制成功');
      // 清除选中的文本
      e.clearSelection();
    });

    clipboard.on('error', (e) => {
      console.error('复制失败:', e);
      message.error('链接复制失败，请手动复制');
    });
  });

  // 在组件卸载时销毁clipboard实例
  onUnmounted(() => {
    if (clipboard) {
      clipboard.destroy();
      clipboard = null;
    }
  });
  // 分享
  function share() {
    // message.success('分享成功');
    console.log('record.value.processInstanceId', record.value);
    console.log(window.location);
    let type =
      record.value.processDefinitionKey == 'Flow_training_program'
        ? 'train'
        : record.value.processDefinitionKey == 'Flow_important'
          ? 'teacher'
          : 'other';
    let id = type == 'other' ? record.value.id || record.value.bizId : record.value.processInstanceId;
    let url = window.location.host + '?redirect=/approvalCenter/wait&processInstanceId=' + id + '&type=' + type;
    modelInfo.url = url;
    // 复制链接到剪贴板
    if (navigator.clipboard && window.isSecureContext) {
      // 现代浏览器的安全上下文
      navigator.clipboard
        .writeText(url)
        .then(() => {
          message.success('链接复制成功');
        })
        .catch((err) => {
          console.error('无法复制文本: ', err);
          message.error('链接复制失败，请手动复制');
        });
    } else {
      // 降级方案
      const textArea = document.createElement('textarea');
      textArea.value = url;
      textArea.style.position = 'fixed';
      textArea.style.opacity = '0';
      document.body.appendChild(textArea);
      textArea.select();
      try {
        document.execCommand('copy');
        message.success('链接复制成功');
      } catch (err) {
        console.error('无法复制文本: ', err);
        message.error('链接复制失败，请手动复制');
      }
      document.body.removeChild(textArea);
    }
    // modelInfo.showCopy = true;
  }
  // 降级处理函数
  function fallbackDownImg() {
    try {
      loading.value = false;
      // 获取要保存的div元素
      const contentDiv: any = document.querySelector('[data-content-ref="true"]');
      if (!contentDiv) {
        message.warning('未找到要下载的内容');
        return;
      }

      // 保存原始样式
      const originalStyle = {
        height: contentDiv.style.height,
        overflow: contentDiv.style.overflow,
        position: contentDiv.style.position,
      };

      // 临时修改样式
      contentDiv.style.height = 'auto';
      contentDiv.style.overflow = 'visible';
      contentDiv.style.position = 'relative';

      // 使用setTimeout避免阻塞主线程
      setTimeout(() => {
        try {
          // 使用html2canvas在主线程处理（简化版）
          const options = {
            scale: 1,
            useCORS: true,
            logging: false,
            backgroundColor: '#ffffff',
            imageTimeout: 5000,
            allowTaint: true,
          };

          html2canvas(contentDiv as HTMLElement, options)
            .then((canvas) => {
              // 恢复原始样式
              Object.assign(contentDiv.style, originalStyle);
              const dataURL = canvas.toDataURL('image/png');
              const link = document.createElement('a');
              link.download = '审批表单_降级版_' + Date.now() + '.png';
              link.href = dataURL;
              link.click();
              message.success('已生成降级版表单图片');
            })
            .catch((error) => {
              console.error('降级处理失败:', error);
              Object.assign(contentDiv.style, originalStyle);
              message.error('降级处理失败，请重试');
            });
        } catch (error) {
          console.error('降级处理异常:', error);
          Object.assign(contentDiv.style, originalStyle);
          message.error('降级处理异常');
        }
      }, 100);
    } catch (error) {
      console.error('降级处理初始化失败:', error);
      message.error('降级处理失败');
    }
  }
</script>

<style scoped lang="less">
  .modelBox {
    padding: 1rem 2.5rem;
  }

  .detail-iframe {
    border: 0;
    width: 100%;
    height: 100%;
    min-height: 500px;
    // -update-begin--author:liaozhiyang---date:20240702---for：【TV360X-1685】通知公告查看出现两个滚动条
    display: block;
    // -update-end--author:liaozhiyang---date:20240702---for：【TV360X-1685】通知公告查看出现两个滚动条
  }

  .br-1 {
    border-right: 1px solid #dcdcdc;
    padding: 12px;
  }

  .flowContent {
    width: 30%;
    height: 100%;
    padding-left: 12px;
  }

  .auditForm {
    width: 100%;
    padding-top: 12px;
    border-top: 1px solid rgba(5, 5, 5, 0.06);
  }

  .infoBox {
    padding: 1.5rem;

    .number {
      font-size: 0.88rem;
      font-weight: 400;
      color: rgba(153, 153, 153, 1);
    }

    .title {
      font-size: 1.25rem;
      font-weight: 400;
      color: rgba(51, 51, 51, 1);
    }

    .secTitle {
      font-size: 0.88rem;
      font-weight: 400;
      color: rgba(51, 51, 51, 1);
    }

    .fileBox {
      width: 100%;
      height: 3rem;
      opacity: 1;
      border-radius: 0.25rem;
      background: rgba(245, 247, 252, 1);
      padding: 0.5rem;
      box-sizing: border-box;

      .icon_32 {
        width: 2rem;
        height: 2rem;
      }

      .fileName {
        font-size: 0.88rem;
        font-weight: 400;
        line-height: 2rem;
        color: rgba(51, 51, 51, 1);
      }

      .less {
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }

      .downText {
        font-size: 0.88rem;
        font-weight: 400;
        line-height: 2rem;
        color: rgba(0, 98, 207, 1);
        cursor: pointer;
      }
    }
  }
</style>
