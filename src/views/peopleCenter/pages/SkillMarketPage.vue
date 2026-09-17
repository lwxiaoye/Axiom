<template>
  <SkillMarketTab @use-skill="handleUseSkill" />
</template>

<script setup lang="ts">
import SkillMarketTab from '../tabs/SkillMarketTab.vue';
import { useCenterContext } from '../centerContext';

defineOptions({ name: 'CenterSkillMarketPage' });

const ctx = useCenterContext();
const { handleUseSkill } = ctx.centerChat;
</script>
