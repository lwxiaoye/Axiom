<!-- eslint-disable vue/multi-word-component-names -->
<template>
  <a-layout-sider class="siderStyle">
    <div class="treeMenu">
      <a-directory-tree @select="select" v-model:selectedKeys="selectedKeys" multiple :tree-data="treeDatas" />
    </div>
  </a-layout-sider>
</template>

<script lang="ts" setup>
  import { ref, h } from 'vue';
  import { ContactsOutlined, FileProtectOutlined, FileAddOutlined, FileDoneOutlined, FileSearchOutlined } from '@ant-design/icons-vue';
  import { useRouter, useRoute } from 'vue-router';
  const router = useRouter();
  const route = useRoute();
  import { getMenu } from '../FlowData.api';

  let treeDatas = ref<any>([]);
  let selectedKeys = ref<any>(['/approvalCenter/all']);

  treeDatas.value = [
    // {
    //   title: '我的待办',
    //   key: '/approvalCenter/wait',
    //   icon: () => h(ContactsOutlined), // 使用渲染函数包裹
    // },
    // {
    //   title: '我的已办',
    //   key: '/approvalCenter/done',
    //   icon: () => h(FileDoneOutlined), // 使用渲染函数包裹
    // },
    // {
    //   title: '我的发起',
    //   key: '/approvalCenter/start',
    //   icon: () => h(FileAddOutlined), // 使用渲染函数包裹
    // },
    // {
    //   title: '我的审批',
    //   key: '/approvalCenter/all',
    //   icon: () => h(FileSearchOutlined), // 使用渲染函数包裹
    // },
  ];

  // 点击方法 - 获取当前节点路径
  function select(selectedKeys: any, e: { selected: boolean; node: any }) {
    if (!e.selected || !e.node) return;
    console.log(selectedKeys, e);
    router.push(selectedKeys[0]);
  }

  function init() {
    getMenu().then((res: any) => {
      res.menu.map((it: any) => {
        if (it.path == '/flow') {
          treeDatas.value = it.children.map((item: any) => {
            let icon: any = null;
            if (item.path == '/approvalCenter/all') {
              icon = FileSearchOutlined;
              // 修正类型问题，icon 初始值为 null，此处应确保类型兼容，这里已使用函数返回 VNode 赋值，类型是匹配的
            } else if (item.path == '/approvalCenter/wait') {
              icon = ContactsOutlined;
              // 修正类型问题，icon 初始值为 null，此处应确保类型兼容，这里已使用函数返回 VNode 赋值，类型是匹配的
            } else if (item.path == '/approvalCenter/done') {
              icon = FileDoneOutlined;
              // 修正类型问题，icon 初始值为 null，此处应确保类型兼容，这里已使用函数返回 VNode 赋值，类型是匹配的
            } else if (item.path == '/approvalCenter/start') {
              icon = FileAddOutlined;
              // 修正类型问题，icon 初始值为 null，此处应确保类型兼容，这里已使用函数返回 VNode 赋值，类型是匹配的
            } else if (item.path == '/approvalCenter/super') {
              icon = FileProtectOutlined;
              // 修正类型问题，icon 初始值为 null，此处应确保类型兼容，这里已使用函数返回 VNode 赋值，类型是匹配的
            }
            return {
              title: item.meta.title,
              key: item.path,
              icon: () => h(icon),
            };
          });
        }
      });
    });
    console.log(route.path, 88888888888888);
    selectedKeys.value = [route.path];
  }

  init();
</script>

<style lang="less" scoped>
  .siderStyle {
    height: 100%;
    background: #fff;
    border-right: 1px solid rgba(235, 235, 235, 1);
    position: relative;
    overflow: hidden;
  }

  .treeMenu {
    padding: 1rem 1.5rem;
    box-sizing: border-box;
    overflow: auto;
  }

  .title {
    line-height: 3rem !important;
    padding-left: 0.5rem;
  }

  .footerBox {
    width: 100%;
    height: 145px;
    padding: 1.5rem;
    box-sizing: border-box;
    position: absolute;
    bottom: 0;
    left: 0;
  }

  .tips {
    font-size: 0.75rem;
    font-weight: 400;
    color: rgba(153, 153, 153, 1);
  }

  .right_icon {
    position: absolute;
    width: 1.25rem;
    height: 1.25rem;
    right: 1rem;
    top: 0;
  }

  .right_icon_left {
    position: absolute;
    width: 1.25rem;
    height: 1.25rem;
    right: 3rem;
    top: 0;
  }

  .addInput {
    width: 6rem;
  }
  .ant-dropdown-link {
    font-size: 0.88rem;
    font-weight: 400;
    line-height: 1.16rem;
    color: rgba(153, 153, 153, 1);
    cursor: pointer;
  }
</style>
