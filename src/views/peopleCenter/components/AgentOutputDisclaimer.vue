<template>
  <p class="agent-output-disclaimer">AI可能会犯错，请核查重要信息</p>
</template>

<style scoped lang="less">
.agent-output-disclaimer {
  width: min(100%, var(--chat-content-max-width, var(--run-reading-width, 820px)));
  margin: 8px auto 0;
  padding: 0;
  color: #8e939d;
  font-size: 12px;
  line-height: 18px;
  pointer-events: none;
  text-align: center;
  /* 只做输入框下方的一行说明，不要实心底/边框，否则在皮肤背景上会变成第二块框。 */
  border: 0;
  background: transparent;
  box-shadow: none;
}
</style>
