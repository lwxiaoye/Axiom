<template>
  <div class="editor">
    <div style="height: 100%" ref="canvas"></div>
  </div>
</template>

<script lang="ts" setup>
  import { onMounted, ref, watch } from 'vue';
  import Modeler from 'bpmn-js/lib/Modeler';
  import 'bpmn-js/dist/assets/diagram-js.css';
  import 'bpmn-js/dist/assets/bpmn-font/css/bpmn.css';
  import { getProcessDiagramByInstance } from '/@/views/flow/flow/FlowInfo.api';
  // 传参
  let props = defineProps<{
    processInstanceId: string;
    indexKey?: string;
  }>();

  const canvas = ref();

  let xml = ref<any>();
  //属性面板容器
  const properties = ref();

  let modeler: any = {};

  onMounted(() => {
    modeler = new Modeler({
      container: canvas.value,
      additionalModules: [
        {
          paletteProvider: ['value', ''], //禁用/清空左侧工具栏
          labelEditingProvider: ['value', ''], //禁用节点编辑
          contextPadProvider: ['value', ''], //禁用图形菜单
          bendpoints: ['value', {}], //禁用连线拖动
          zoomScroll: ['value', ''], //禁用滚动
          // moveCanvas: ['value', ''], //禁用拖动整个流程图
          move: ['value', ''], //禁用单个图形拖动
        },
      ],
    });
    if (props.processInstanceId) {
      getProcessDiagramByInstance(props.processInstanceId).then((res: any) => {
        xml.value = res.replace(/<!\[CDATA\[(.+?)]]>/g, function (match: any, str: any) {
          return str.replace(/</g, '&lt;');
        });
        modeler.importXML(xml.value);

        setTimeout(() => {
          let arrs: any = document.getElementsByClassName('djs-element djs-shape');
          console.log(arrs);
          let number = 0;
          let lastKey = '';
          arrs.forEach((it: any) => {
            let key = it.getAttribute('data-element-id');
            console.log(key);
            if (key == props.indexKey) {
              it.classList.add('color_round_index');
            } else {
              number++;
            }
            lastKey = key;
          });
          // if(number==arrs.length){
          //   arrs[number-1].classList.add('color_round_index')
          // }
        }, 100);
      });
    }
    // modeler.on('selection.changed', (e:any) => {
    //     console.log(e)
    //     const element = e.newSelection[0]
    //     console.log(element)
    // })

    // modeler.on('element.changed', (e:any) => {
    //     console.log(e)
    //     const { element } = e
    //     console.log(element)
    // })
    //     const modeler = new Modeler({ container: canvas.value });
    //     //加上这一句,否则无法添加节点元素

    // modeler.createDiagram();
  });

  // function save(){
  //   modeler.saveXML({ format: true })
  //     .then((res:any)=>{
  //       console.log(res.xml)
  //       const dataTrack = 'bpmn'
  //         const a = document.createElement('a')
  //         const name = `diagram.${dataTrack}`
  //         a.setAttribute(
  //           'href',
  //           `data:application/bpmn20-xml;charset=UTF-8,${encodeURIComponent(res.xml)}`
  //         )
  //         a.setAttribute('target', '_blank')
  //         a.setAttribute('dataTrack', `diagram:download-${dataTrack}`)
  //         a.setAttribute('download', name)
  //         document.body.appendChild(a)
  //         a.click()
  //         document.body.removeChild(a)
  //     })
  // }

  watch(
    () => props.indexKey,
    (newValue, oldValue) => {
      if (props.indexKey) {
        setTimeout(() => {
          let arrs: any = document.getElementsByClassName('djs-element');
          console.log(arrs);
          let number = 0;
          let lastKey = '';
          arrs.forEach((it: any) => {
            let key = it.getAttribute('data-element-id');
            console.log(key);
            if (key == props.indexKey) {
              it.classList.add('color_round_index');
            } else {
              it.classList.remove('color_round_index');
              number++;
            }
            lastKey = key;
          });
        }, 100);
      }
    },
    { immediate: true }
  );
</script>

<style lang="less" scoped>
  .editor {
    width: 100%;
    height: 100%;
    display: grid;
    //grid-template-columns: minmax(300px, 1fr) 300px;
    //   background-color: #000;
    //position: fixed;
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
</style>
