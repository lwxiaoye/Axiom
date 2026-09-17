<template>
  <div class="bpmn relative">
    <div ref="canvas"></div>
    <!--    <div ref="properties"></div>-->

    <!--    <div class="btnGroup flex p-16">-->
    <!--      <a-button class="color_w btn" v-loading="state.load" color="#54c697" @click="save()">保存</a-button>-->

    <!--      <a-button class="color_w btn" v-loading="state.load" color="#54c697" @click="down()">下载</a-button>-->
    <!--      <a-button @click="goBack(-1)">返回</a-button>-->
    <!--    </div>-->
    <div class="rightBox" :style="clientHeight()" v-show="state.show">
      <a-collapse v-model:activeKey="activeKey">
        <a-collapse-panel key="1" header="基本设置（用户任务）">
          <BasicForm @register="registerForm" @change-fn="changeFn" ref="formRef" name="flowItemForm" />
        </a-collapse-panel>
        <a-collapse-panel key="2" header="审批人员设置">
          <BasicForm @register="registerForm1" @change-fn="changeFn1" ref="formRef" name="flowItemForm1" />
        </a-collapse-panel>
        <a-collapse-panel key="3" header="审核人员空处理">
          <BasicForm @register="registerForm2" @change-fn="changeFn2" ref="formRef" name="flowItemForm2" />
        </a-collapse-panel>
      </a-collapse>
    </div>
  </div>
</template>

<script lang="ts" setup>
  // import { defineComponent } from 'vue';
  import { ElMessage } from 'element-plus';
  import { BasicForm, useForm } from '@/components/Form';
  import { formSchema, formSchema1, formSchema2, getDefaultXml } from './BpmnView.data';
  import { onMounted, ref, reactive } from 'vue';
  import { BpmnPropertiesPanelModule, BpmnPropertiesProviderModule, CamundaPlatformPropertiesProviderModule } from 'bpmn-js-properties-panel';
  // 此问题通常是因为依赖未安装，可先尝试安装依赖：npm install camunda-bpmn-moddle
  import camundaModdleDescriptor from 'camunda-bpmn-moddle/resources/camunda.json';
  import { getBpmnDetails } from '../../flow/FlowInfo.api';
  import { clientHeight } from '@/utils/clientHeight';

  import Modeler from 'bpmn-js/lib/Modeler';
  import 'bpmn-js/dist/assets/diagram-js.css';
  import 'bpmn-js/dist/assets/bpmn-font/css/bpmn.css';
  import '@/assets/css/properties-panel.css';

  const activeKey = ref([1, 2, 3]);

  const [registerForm, { setProps, resetFields, setFieldsValue, getFieldsValue }] = useForm({
    labelWidth: 100,
    schemas: formSchema,
    showActionButtonGroup: false,
    baseColProps: { span: 24 },
  });
  const [registerForm1, { setProps: setProps1, resetFields: resetFields1, setFieldsValue: setFieldsValue1, getFieldsValue: getFieldsValue1 }] =
    useForm({
      labelWidth: 100,
      schemas: formSchema1,
      showActionButtonGroup: false,
      baseColProps: { span: 24 },
    });
  const [registerForm2, { setProps: setProps2, resetFields: resetFields2, setFieldsValue: setFieldsValue2, getFieldsValue: getFieldsValue2 }] =
    useForm({
      labelWidth: 105,
      schemas: formSchema2,
      showActionButtonGroup: false,
      baseColProps: { span: 24 },
    });

  // import { getAllRole, getDeptsTree } from '@/utils/api';

  import customTranslate from '@/assets/translate';

  // import { useRoute, useRouter } from 'vue-router';

  // const router = useRouter();
  // const route = useRoute();
  // 传参
  let props = defineProps<{
    processDefinitionKey: string | number | null; //流程id
    appName: string; //流程名称
    processInstanceId?: string;
    indexKey?: string;
  }>();

  let state = reactive<any>({
    userTaskValue: {},
    show: false,
    userTaskId: '',
    load: false,
  });
  const canvas = ref();
  // const properties = ref<any>();
  // let bpmnForm = ref<any>();
  let chooseModel = ref<any>();

  let modeler: any = {};
  let bpmnFactory: any = {};
  let modeling: any = {};
  // let ids = ref<any>();
  let isDetail = ref<boolean>(false);
  //cmd 初始化用户任务面板默认值
  function initUserTaskValue() {
    state.userTaskValue = {
      auditType: '',
      roleIds: [],
      deptIds: [],
      userIds: '',
      alternativeUserIds: '',
      nullType: 'isSkip',
      deptsLeader: '',
      deptsFilter: '',
    };
    resetFields();
    resetFields1();
    resetFields2();
  }
  //cmd 初始化用户任务extensionElements
  function initUserTaskExtension(element: any) {
    //不存在extensionElements则初始化
    element.businessObject.extensionElements = bpmnFactory.create('bpmn:ExtensionElements', {
      values: [
        bpmnFactory.create('camunda:Properties', {
          values: [
            bpmnFactory.create('camunda:Property', {
              name: 'remark',
              value: '',
            }),
          ],
        }),
        bpmnFactory.create('camunda:TaskListener', {
          class: import.meta.env.VITE_BPMN_TASK_LISTENER_CLASS || '',
          event: 'create',
          //默认节点匹配以上默认数据
          fields: [
            bpmnFactory.create('camunda:Field', {
              name: 'nullType',
              string: 'isSkip',
            }),
          ],
        }),
      ],
    });
  }

  const clickUserTask = async (element: any) => {
    //初始化用户任务面板默认值
    initUserTaskValue();
    //回显节点

    console.log('element', element.id);

    if (!element.businessObject.extensionElements) {
      //初始化用户任务extensionElements
      initUserTaskExtension(element);
    } else {
      element.businessObject.extensionElements.values.forEach((item: any) => {
        if (item.$type === 'camunda:Properties') {
          //回显节点备注
          state.userTaskValue.remark = item.values[0].value;
        }
        if (item.$type === 'camunda:TaskListener') {
          item.fields &&
            item.fields.map((it: any) => {
              if (it.string && it.string != 'null') {
                if (it.name == 'roleIds') {
                  state.userTaskValue.roleIds = it.string.split(',');
                } else if (it.name == 'deptIds') {
                  state.userTaskValue.deptIds = it.string.split(',');
                } else {
                  state.userTaskValue[it.name] = it.string;
                }
              }
            });
        }
      });
      state.userTaskValue.name = element.businessObject.name;
    }
    setFieldsValue({
      ...state.userTaskValue,
    });
    setFieldsValue1({
      ...state.userTaskValue,
    });
    setFieldsValue2({
      ...state.userTaskValue,
    });
    // state.show = true;
    console.log(element, 8888);
    if (element.id == 'Activity_0jmlhay') {
      state.show = false;
    } else {
      state.show = true;
      if (isDetail.value) {
        await setProps({ disabled: true });
        await setProps1({ disabled: true });
        await setProps2({ disabled: true });
      }
    }
  };
  //初始化流程图
  function init(processDefinitionKey: any, _isDetail: boolean) {
    console.log('1111', _isDetail);
    state.show = false;
    isDetail.value = _isDetail ? true : false;

    //通过接口获取流程图
    getBpmnDetails(processDefinitionKey).then((res: any) => {
      if (res) {
        modeler.importXML(res);
      } else {
        modeler.importXML(getDefaultXml(props.processDefinitionKey, props.appName));
      }
      modeling = modeler.get('modeling');
      //选择变化方法监听
      modeler.on('selection.changed', (e: any) => {
        console.log(e);
        const element = e.newSelection[0];
        if (element && element.businessObject) {
          chooseModel.value = element;
          if (element.businessObject.$type == 'bpmn:UserTask') {
            clickUserTask(element);
          } else {
            state.show = false;
          }
        }
        // if (element && element.businessObject && element.businessObject.$type == 'bpmn:UserTask') {
        //   //是用户任务
        //   modeler.propertiesPanel = {};
        //   state.userTaskValue = JSON.parse(JSON.stringify(element.businessObject));
        //   chooseModel.value = element;
        //
        //   let arr: any = [];
        //   let userids: any = [];
        //   let arrs: any = [];
        //   let alternativeUserNames: any = [];
        //   state.userTaskValue.roleIds = [];
        //   state.userTaskValue.deptIds = [];
        //   console.log('userTaskValue', state.userTaskValue);
        //   // 判断是任务监听-设置属性
        //   if (
        //     element.businessObject.extensionElements &&
        //     element.businessObject.extensionElements.values &&
        //     element.businessObject.extensionElements.values.length
        //   ) {
        //     let taskListener = element.businessObject.extensionElements.values[0];
        //
        //     taskListener.fields &&
        //       taskListener.fields.map((it: any) => {
        //         if (it.name == 'alternativeUserNames') {
        //           if (it.string && it.string != 'null') {
        //             state.userTaskValue.alternativeUserNames = it.string.split(',');
        //           }
        //         }
        //         if (it.name == 'roleIds' && it.string != 'null') {
        //           state.userTaskValue.roleIds = it.string.split(',').map(Number);
        //           console.log('roleIds1');
        //         } else {
        //           state.userTaskValue.roleIds = [];
        //           console.log('roleIds');
        //         }
        //         if (it.name == 'deptIds' && it.string != 'null') {
        //           state.userTaskValue.deptIds = it.string.split(',').map(Number);
        //           console.log('roleIds1');
        //         } else {
        //           state.userTaskValue.deptIds = [];
        //           console.log('roleIds');
        //         }
        //         if (it.name == 'param' && it.string != 'null') {
        //           state.userTaskValue.param = it.string;
        //         }
        //         if (it.name == 'userScope' && it.string != 'null') {
        //           state.userTaskValue.userScope = it.string;
        //         }
        //         if (it.name == 'isSkip' && it.string != 'null') {
        //           state.userTaskValue.isSkip = it.string ? it.string : 'false';
        //         }
        //       });
        //
        //     state.userList.map((it: any) => {
        //       userids.map((its: any) => {
        //         if (it.userId == its) {
        //           arr.push(it);
        //         }
        //       });
        //       alternativeUserNames.map((its: any) => {
        //         if (it.userId == its) {
        //           arrs.push(it);
        //         }
        //       });
        //     });
        //   }
        //
        //   console.log('1111000000000000000000', state.userTaskValue);
        //
        //   if (!state.userTaskValue.isSkip) {
        //     state.userTaskValue.isSkip = 'false';
        //   }
        //   setFieldsValue({
        //     ...state.userTaskValue,
        //   });
        //   setFieldsValue1({
        //     ...state.userTaskValue,
        //   });
        //   console.log('11111', state.userTaskValue);
        //   state.show = true;
        // } else if (element && element.businessObject && element.businessObject.$type == 'bpmn:SendTask') {
        //   // 10-09 新增sendTask模块-解析选择后参数回填
        //
        //   state.userTaskValue = JSON.parse(JSON.stringify(element.businessObject));
        //   chooseModel.value = element;
        //
        //   state.meta.map((it: any) => {
        //     if (it.key == '2' || it.key == 'isSkip' || it.key == 'alternativeUserNames') {
        //       it.hide = true;
        //     }
        //   });
        //
        //   let arr: any = [];
        //   let userids: any = [];
        //   let arrs: any = [];
        //   let alternativeUserNames: any = [];
        //
        //   // 判断是任务监听-设置属性
        //   if (
        //     element.businessObject.extensionElements &&
        //     element.businessObject.extensionElements.values &&
        //     element.businessObject.extensionElements.values.length
        //   ) {
        //     let taskListener = element.businessObject.extensionElements.values;
        //
        //     taskListener.map((it: any) => {
        //       console.log(999, it);
        //       if (it.name == 'userNames') {
        //         if (it.string && it.string != 'null') {
        //           userids = it.string.split(',');
        //         }
        //       }
        //       if (it.name == 'alternativeUserNames') {
        //         if (it.string && it.string != 'null') {
        //           state.userTaskValue.alternativeUserNames = it.string.split(',');
        //         }
        //       }
        //       if (it.name == 'roleIds' && it.string != 'null') {
        //         state.userTaskValue.roleIds = it.string.split(',');
        //       }
        //       if (it.name == 'deptIds' && it.string != 'null') {
        //         state.userTaskValue.deptIds = it.string.split(',');
        //       }
        //       if (it.name == 'param' && it.string != 'null') {
        //         state.userTaskValue.param = it.string;
        //       }
        //       if (it.name == 'userScope' && it.string != 'null') {
        //         state.userTaskValue.userScope = it.string;
        //       }
        //       if (it.name == 'isSkip' && it.string != 'null') {
        //         state.userTaskValue.isSkip = it.string ? it.string : 'false';
        //       }
        //     });
        //
        //     state.userList.map((it: any) => {
        //       userids.map((its: any) => {
        //         if (it.userId == its) {
        //           arr.push(it);
        //         }
        //       });
        //       alternativeUserNames.map((its: any) => {
        //         if (it.userId == its) {
        //           arrs.push(it);
        //         }
        //       });
        //     });
        //   }
        //
        //   state.show = true;
        //   if (!state.userTaskValue.isSkip) {
        //     state.userTaskValue.isSkip = 'false';
        //   }
        //   state.userTaskValue.users = JSON.parse(JSON.stringify(arr));
        //   state.userTaskValue.alternativeUserNames = JSON.parse(JSON.stringify(arrs));
        //   // state.show = false
        // } else {
        //   state.show = false;
        //
        //   let value = getFieldsValue();
        //   let value1 = getFieldsValue1();
        //   console.log('getFieldsValue1', value, value1);
        // }
      });
    });
  }

  // const customPalette = {
  //   getPaletteEntries() {
  //     // 1. 获取默认的 Palette 配置
  //     const defaultPalette = this._originalPalette.getPaletteEntries();

  //     // 2. 移除不需要的 "Task"
  //     delete defaultPalette['create.task'];

  //     // 3. 确保 "User Task" 存在（或自定义它的行为）
  //     defaultPalette['create.user-task'] = {
  //       group: 'activity',
  //       className: 'bpmn-icon-user-task',
  //       title: 'User Task',
  //       action: {
  //         click: (event) => {
  //           const shape = this._elementFactory.createShape({
  //             type: 'bpmn:UserTask',
  //             businessObject: {
  //               name: 'User Task',
  //             },
  //           });
  //           this._canvas.addShape(shape, { x: event.x, y: event.y });
  //         },
  //       },
  //     };

  //     return defaultPalette;
  //   },
  // };

  onMounted(() => {
    //     //初始化 modeler 编辑器
    modeler = new Modeler({
      container: canvas.value,
      //属性面板
      // propertiesPanel: {
      //   parent: properties.value,
      // },
      additionalModules: [
        BpmnPropertiesPanelModule,
        BpmnPropertiesProviderModule,
        CamundaPlatformPropertiesProviderModule,
        {
          translate: ['value', customTranslate],
        },
      ],
      moddleExtensions: {
        camunda: camundaModdleDescriptor,
      },
    });
    bpmnFactory = modeler.get('bpmnFactory');
  });

  const changeFn = (schema: any) => {
    let field = schema._value.field;
    let value = getFieldsValue();
    let element = chooseModel.value;
    if (field === 'remark') {
      element.businessObject.extensionElements.values.forEach((item: any) => {
        if (item.$type === 'camunda:Properties') {
          item.values[0].value = value[field];
        }
      });
    }
    // 获取元素正确的方式
    if (field === 'name') {
      const elementRegistry = modeler.get('elementRegistry');
      modeling = modeler.get('modeling');
      modeling.updateProperties(elementRegistry.get(element.id), {
        name: value[field],
      });
    }
  };
  const changeFn1 = (schema: any) => {
    let field = schema._value.field;
    let value = getFieldsValue1();
    change(field, value);
    console.log('changeFn1', field, value);
    if (field === 'auditType') {
      let data: any = {};
      if (value[field] === 'user') {
        data.roleIds = [];
        data.deptIds = [];
        data.deptsLeader = false;
      } else if (value[field] === 'role') {
        data.userIds = '';
        data.deptIds = [];
        data.deptsLeader = false;
      } else if (value[field] === 'dept') {
        data.userIds = '';
        data.roleIds = [];
        data.deptsFilter = false;
      } else if (value[field] === 'leader') {
        data.deptsLeader = false;
        data.deptsFilter = false;
        data.userIds = '';
        data.roleIds = [];
        data.deptIds = [];
      }

      let keys = Object.keys(data);
      let element = chooseModel.value;
      element.businessObject.extensionElements.values.forEach((item: any) => {
        if (item.$type === 'camunda:TaskListener') {
          item.fields.forEach((it: any) => {
            if (keys.indexOf(it.name) > -1) {
              it.string = 'null';
            }
          });
        }
      });
      setFieldsValue1(data);
    }
  };
  const changeFn2 = (schema: any) => {
    let field = schema._value.field;
    let value = getFieldsValue2();
    change(field, value);
    if (field === 'nullType') {
      if (value[field] === 'isSkip') {
        setFieldsValue2({
          alternativeUserIds: '',
        });
      }

      let element = chooseModel.value;
      element.businessObject.extensionElements.values.forEach((item: any) => {
        if (item.$type === 'camunda:TaskListener') {
          item.fields.forEach((it: any) => {
            if (it.name === 'alternativeUserIds') {
              it.string = 'null';
            }
          });
        }
      });
    }
  };
  const change = (field: any, value: any) => {
    let element = chooseModel.value;
    element.businessObject.extensionElements.values.forEach((item: any) => {
      if (item.$type === 'camunda:TaskListener') {
        let has = false;
        item.fields.forEach((it: any) => {
          if (it.name === field) {
            it.string = value[field];
            has = true;
          }
        });
        if (!has) {
          item.fields.push(
            bpmnFactory.create('camunda:Field', {
              name: field,
              string: value[field] || 'null',
            })
          );
        }
      }
    });
  };

  // 设置属性
  // function setObj(info: any) {
  //   let obj = info.data;
  //   console.log('obj', obj, obj.deptIds);

  //   modeling = modeler.get('modeling');
  //   let elementRegistry = modeler.get('elementRegistry');
  //   let bpmnFactory = modeler.get('bpmnFactory');
  //   let shape = elementRegistry.get(chooseModel.value.id);

  //   // 是否新增的
  //   if (!shape.businessObject.extensionElements) {
  //     if (obj.$type == 'bpmn:UserTask') {
  //       shape.businessObject.extensionElements = bpmnFactory.create('bpmn:ExtensionElements', {
  //         values: [
  //           bpmnFactory.create('camunda:TaskListener', {
  //             class: 'org.jeecg.modules.flow.camunda.listener.UserTaskListener',
  //             event: 'create',
  //           }),
  //         ],
  //       });
  //     }

  //     // 10-09 新增sendTask模块
  //     if (obj.$type == 'bpmn:SendTask') {
  //       modeling.updateProperties(shape, {
  //         name: obj.name,
  //         class: import.meta.env.VITE_BPMN_SEND_DELEGATE_CLASS || '',
  //       });
  //     }
  //   }

  //   if (obj.$type == 'bpmn:UserTask') {
  //     let taskListener = shape.businessObject.extensionElements.values[0];
  //     let fields = [];

  //     fields.push(
  //       bpmnFactory.create('camunda:Field', {
  //         name: 'param',
  //         string: obj.param && obj.param.length > 0 ? obj.param : 'null',
  //       })
  //     );

  //     fields.push(
  //       bpmnFactory.create('camunda:Field', {
  //         name: 'userNames',
  //         string:
  //           obj.users && obj.users.length > 0
  //             ? obj.users
  //                 .map((it: any) => {
  //                   return it.userId;
  //                 })
  //                 .join(',')
  //             : 'null',
  //       })
  //     );

  //     fields.push(
  //       bpmnFactory.create('camunda:Field', {
  //         name: 'roleIds',
  //         string: obj.roleIds && obj.roleIds.length > 0 ? obj.roleIds.join(',') : 'null',
  //       })
  //     );

  //     fields.push(
  //       bpmnFactory.create('camunda:Field', {
  //         name: 'deptIds',
  //         string: obj.deptIds && obj.deptIds.length > 0 ? obj.deptIds.join(',') : 'null',
  //       })
  //     );

  //     fields.push(
  //       bpmnFactory.create('camunda:Field', {
  //         name: 'userScope',
  //         string: obj.userScope ? obj.userScope + '' : 'null',
  //       })
  //     );
  //     if (info.key == 'isSkip' && obj.isSkip == 'true') {
  //       fields.push(
  //         bpmnFactory.create('camunda:Field', {
  //           name: 'isSkip',
  //           string: obj.isSkip ? obj.isSkip + '' : 'null',
  //         })
  //       );
  //       bpmnForm.value.updateFormData({ alternativeUserNames: [] });
  //       fields.push(
  //         bpmnFactory.create('camunda:Field', {
  //           name: 'alternativeUserNames',
  //           string: 'null',
  //         })
  //       );
  //     } else if (info.key == 'isSkip') {
  //       fields.push(
  //         bpmnFactory.create('camunda:Field', {
  //           name: 'isSkip',
  //           string: obj.isSkip ? obj.isSkip + '' : 'null',
  //         })
  //       );
  //     } else if (info.key == 'alternativeUserNames') {
  //       fields.push(
  //         bpmnFactory.create('camunda:Field', {
  //           name: 'isSkip',
  //           string: 'false',
  //         })
  //       );
  //       bpmnForm.value.updateFormData({ isSkip: 'false' });
  //       fields.push(
  //         bpmnFactory.create('camunda:Field', {
  //           name: 'alternativeUserNames',
  //           string:
  //             obj.alternativeUserNames && obj.alternativeUserNames.length > 0
  //               ? obj.alternativeUserNames
  //                   .map((it: any) => {
  //                     return it.userId;
  //                   })
  //                   .join(',')
  //               : 'null',
  //         })
  //       );
  //     }

  //     console.log('fields', fields);

  //     taskListener.fields = fields;
  //   }
  //   // 10-09 新增sendTask模块-解析输入的字段
  //   if (obj.$type == 'bpmn:SendTask') {
  //     // let taskListeners = shape.businessObject.extensionElements.values
  //     let fields = [];

  //     fields.push(
  //       bpmnFactory.create('camunda:Field', {
  //         name: 'param',
  //         string: obj.param && obj.param.length > 0 ? obj.param : 'null',
  //       })
  //     );

  //     fields.push(
  //       bpmnFactory.create('camunda:Field', {
  //         name: 'userNames',
  //         string:
  //           obj.users && obj.users.length > 0
  //             ? obj.users
  //                 .map((it: any) => {
  //                   return it.userId;
  //                 })
  //                 .join(',')
  //             : 'null',
  //       })
  //     );

  //     fields.push(
  //       bpmnFactory.create('camunda:Field', {
  //         name: 'roleIds',
  //         string: obj.roleIds && obj.roleIds.length > 0 ? obj.roleIds.join(',') : 'null',
  //       })
  //     );

  //     fields.push(
  //       bpmnFactory.create('camunda:Field', {
  //         name: 'deptIds',
  //         string: obj.deptIds && obj.deptIds.length > 0 ? obj.deptIds.join(',') : 'null',
  //       })
  //     );

  //     fields.push(
  //       bpmnFactory.create('camunda:Field', {
  //         name: 'userScope',
  //         string: obj.userScope ? obj.userScope + '' : 'null',
  //       })
  //     );
  //     if (info.key == 'isSkip' && obj.isSkip == 'true') {
  //       fields.push(
  //         bpmnFactory.create('camunda:Field', {
  //           name: 'isSkip',
  //           string: obj.isSkip ? obj.isSkip + '' : 'null',
  //         })
  //       );
  //       bpmnForm.value.updateFormData({ alternativeUserNames: [] });
  //       fields.push(
  //         bpmnFactory.create('camunda:Field', {
  //           name: 'alternativeUserNames',
  //           string: 'null',
  //         })
  //       );
  //     } else if (info.key == 'isSkip') {
  //       fields.push(
  //         bpmnFactory.create('camunda:Field', {
  //           name: 'isSkip',
  //           string: obj.isSkip ? obj.isSkip + '' : 'null',
  //         })
  //       );
  //     } else if (info.key == 'alternativeUserNames') {
  //       fields.push(
  //         bpmnFactory.create('camunda:Field', {
  //           name: 'isSkip',
  //           string: 'false',
  //         })
  //       );
  //       bpmnForm.value.updateFormData({ isSkip: 'false' });
  //       fields.push(
  //         bpmnFactory.create('camunda:Field', {
  //           name: 'alternativeUserNames',
  //           string:
  //             obj.alternativeUserNames && obj.alternativeUserNames.length > 0
  //               ? obj.alternativeUserNames
  //                   .map((it: any) => {
  //                     return it.userId;
  //                   })
  //                   .join(',')
  //               : 'null',
  //         })
  //       );
  //     }

  //     console.log('fields', 888888, fields);
  //     shape.businessObject.extensionElements = bpmnFactory.create('bpmn:ExtensionElements', {
  //       values: fields,
  //     });
  //     // taskListeners = fields
  //   }

  //   modeling.updateProperties(shape, {
  //     name: obj.name,
  //   });
  // }

  // function upFilesFun(xml: any) {
  //   // const {file} = option;
  //   let formData = new FormData();
  //   formData.append('file', new Blob([xml]), 'new.bpmn');
  //   // let url = '/flow/task/deploy';
  //   // upFilesInfo(url, formData)
  //   //   .then((response: any) => {
  //   //     state.load = false;
  //   //     ElMessage({
  //   //       message: '保存成功',
  //   //       type: 'success',
  //   //     });
  //   //     // goBack();
  //   //   })
  //   //   .catch(() => {
  //   //     state.load = false;
  //   //   });
  // }

  defineExpose({
    getFile,
    setFile,
    init,
  });

  //获取当前xml文件
  async function getFile(fn: any) {
    //校验所有流程必填项

    // 获取流程模型
    const definitions = modeler.getDefinitions();

    // 获取所有流程元素
    const elements = definitions.rootElements[0].flowElements;

    let requiredField = false;
    elements.forEach((element) => {
      // 判断元素类型是否为 bpmn:UserTask
      if (element.$type === 'bpmn:UserTask') {
        if (element.id != 'Activity_0jmlhay') {
          //校验值
          let userTaskValue: any = {};
          element.extensionElements.values.forEach((item: any) => {
            if (item.$type === 'camunda:Properties') {
              //回显节点备注
              userTaskValue.remark = item.values[0]?.value || '';
            }
            if (item.$type === 'camunda:TaskListener') {
              item.fields &&
                item.fields.map((it: any) => {
                  if (it.name == 'auditType') {
                    if (!it.string) {
                      it.string = 'null';
                    }
                    userTaskValue.auditType = it.string || 'null';
                  }
                  if (it.string && it.string != 'null') {
                    if (it.name == 'roleIds') {
                      userTaskValue.roleIds = it.string.split(',');
                      if (!userTaskValue.roleIds || userTaskValue.roleIds.length === 0) {
                        userTaskValue.roleIds = 'null';
                      }
                    } else if (it.name == 'deptIds') {
                      userTaskValue.deptIds = it.string.split(',');
                      if (!userTaskValue.deptIds || userTaskValue.deptIds.length === 0) {
                        userTaskValue.deptIds = 'null';
                      }
                    } else if (it.name == 'userIds') {
                      userTaskValue.userIds = it.string.split(',');
                      if (!userTaskValue.userIds || userTaskValue.userIds.length === 0) {
                        userTaskValue.userIds = 'null';
                      }
                    } else {
                      if (it.name == 'userIds') {
                      }
                      // 确保所有值都是字符串类型
                      userTaskValue[it.name] = String(it.string || '');
                    }
                  } else {
                    it.string = 'null';
                    userTaskValue[it.name] = 'null';
                  }
                });
            }
            userTaskValue.name = element.name || '';
          });

          if (!userTaskValue.name) {
            requiredField = true;
          }
          if (!userTaskValue.auditType && userTaskValue.startUserSpecify == 'false') {
            requiredField = true;
          }
          if (
            userTaskValue.auditType == 'user' &&
            (!userTaskValue.userIds || userTaskValue.userIds.length == 0) &&
            userTaskValue.startUserSpecify == 'false'
          ) {
            requiredField = true;
          }
          if (
            userTaskValue.auditType == 'role' &&
            (!userTaskValue.roleIds || userTaskValue.roleIds.length == 0) &&
            userTaskValue.startUserSpecify == 'false'
          ) {
            requiredField = true;
          }
          if (
            userTaskValue.auditType == 'dept' &&
            (!userTaskValue.deptIds || userTaskValue.deptIds.length == 0) &&
            userTaskValue.startUserSpecify == 'false'
          ) {
            requiredField = true;
          }

          if (!userTaskValue.nullType) {
            requiredField = true;
          }
          if (userTaskValue.nullType == 'user' && (!userTaskValue.alternativeUserIds || userTaskValue.alternativeUserIds.length == 0)) {
            requiredField = true;
          }
        }
      }
    });

    if (requiredField) {
      ElMessage({
        message: '请检查流程必填项',
        type: 'error',
      });
    } else {
      modeler
        .saveXML({ format: true })
        .then((res: any) => {
          console.log(res.xml);
          fn && fn(res.xml);
        })
        .catch((res: any) => {
          console.log(res, 122222);
          ElMessage({
            message: res,
            type: 'warning',
          });
        });
    }
  }
  async function setFile(xml: string) {
    if (!xml || !xml.includes('bpmn:definitions')) {
      console.error('Invalid BPMN XML content');
      ElMessage.error('无效的BPMN XML内容');
      return;
    }

    try {
      const { warnings } = await modeler.importXML(xml);

      if (warnings && warnings.length) {
        console.warn('BPMN导入警告:', warnings);
        ElMessage.warning('BPMN导入存在警告，请检查流程定义');
      }

      // 确保视图正确渲染
      modeler.get('canvas').zoom('fit-viewport');
    } catch (err: any) {
      console.error('BPMN导入失败:', err);
      ElMessage.error('导入BPMN流程图失败: ' + (err.message || '未知错误'));

      // 尝试使用默认XML作为回退
      try {
        await modeler.importXML(getDefaultXml(props.processDefinitionKey, props.appName));
      } catch (fallbackErr) {
        console.error('回退默认BPMN也失败:', fallbackErr);
      }
    }
  }

  // function save() {
  //   modeler
  //     .saveXML({ format: true })
  //     .then((res: any) => {
  //       console.log(res.xml);
  //       upFilesFun(res.xml);
  //     })
  //     .catch((res: any) => {
  //       ElMessage({
  //         message: res,
  //         type: 'warning',
  //       });
  //     });
  // }

  // function down() {
  //   // let bpmnFactory = modeler.get('process');
  //   let elementRegistry = modeler.get('elementRegistry');
  //   let shape = elementRegistry.get(route.query.id);
  //   modeler.saveXML({ format: true }).then((res: any) => {
  //     const dataTrack = 'bpmn';
  //     const a = document.createElement('a');
  //     const name = `${shape.businessObject.name}.${dataTrack}`;
  //     a.setAttribute('href', `data:application/bpmn20-xml;charset=UTF-8,${encodeURIComponent(res.xml)}`);
  //     a.setAttribute('target', '_blank');
  //     a.setAttribute('dataTrack', `diagram:download-${dataTrack}`);
  //     a.setAttribute('download', name);
  //     document.body.appendChild(a);
  //     a.click();
  //     document.body.removeChild(a);
  //   });
  // }
</script>

<style lang="less" scoped>
  .bpmn {
    width: 100%;
    height: 100%;
    display: grid;
    grid-template-columns: minmax(400px, 1fr) 400px;
    //   background-color: #000;
  }

  .bio-properties-panel {
    --select-template-background-color: var(--color-blue-205-100-50);
    --select-template-hover-background-color: var(--color-blue-205-100-45);
    --select-template-fill-color: var(--color-white);
    --select-template-label-color: var(--color-white);

    --unknown-template-background-color: var(--color-red-360-100-45);
    --unknown-template-hover-background-color: var(--color-red-360-100-40);

    --update-available-text-color: var(--color-grey-225-10-55);
  }

  .bio-properties-panel-templates-group .bio-properties-panel-group-header-button:not(.bio-properties-panel-arrow) {
    padding-right: 6px;
    padding-left: 9px;
    border-radius: 11px;
  }

  .bio-properties-panel-applied-template-button .bio-properties-panel-group-header-button,
  .bio-properties-panel-template-update-available .bio-properties-panel-group-header-button,
  .bio-properties-panel-group-header-button.bio-properties-panel-select-template-button {
    background-color: var(--select-template-background-color);
    color: var(--select-template-label-color);
    fill: var(--select-template-fill-color);
  }

  .bio-properties-panel-applied-template-button .bio-properties-panel-group-header-button:hover,
  .bio-properties-panel-template-update-available .bio-properties-panel-group-header-button:hover,
  .bio-properties-panel-group-header-button.bio-properties-panel-select-template-button:hover {
    background-color: var(--select-template-hover-background-color);
  }

  .bio-properties-panel-templates-group .bio-properties-panel-group-header-button * {
    color: inherit;
  }

  .bio-properties-panel-templates-group .bio-properties-panel-group-header-button * + * {
    margin-left: 2px;
  }

  .bio-properties-panel-group-header-button.bio-properties-panel-select-template-button:last-child {
    padding-right: 9px;
    padding-left: 6px;
    margin-right: 22px;
  }

  .bio-properties-panel-template-update-available:last-child,
  .bio-properties-panel-applied-template-button:last-child,
  .bio-properties-panel-template-not-found:last-child {
    margin-right: 32px;
  }

  .bio-properties-panel-template-not-found-text,
  .bio-properties-panel-remove-template {
    color: var(--text-error-color);
  }

  .bio-properties-panel-template-not-found .bio-properties-panel-group-header-button {
    background-color: var(--unknown-template-background-color);
    color: var(--select-template-label-color);
    fill: var(--select-template-fill-color);
  }

  .bio-properties-panel-template-not-found .bio-properties-panel-group-header-button:hover {
    background-color: var(--unknown-template-hover-background-color);
  }

  .bio-properties-panel-template-update-available-text {
    color: var(--update-available-text-color);
  }

  .bio-properties-panel-template-not-found-text,
  .bio-properties-panel-template-update-available-text {
    width: 216px;
  }

  .rightBox {
    background-color: #f9f9f9;
    border: 1px solid #fff;
    box-shadow: 0 10px 10px rgba(0, 0, 0, 0.2);
    width: 400px;
    height: calc(100%);
    padding: 12px;
    box-sizing: border-box;
    z-index: 99;
    position: absolute;
    right: 0;
  }
</style>
