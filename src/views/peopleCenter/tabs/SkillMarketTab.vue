<template>
  <section class="skill-square-section">
    <SkillSquare @use-skill="(skill) => emit('useSkill', skill)" />
  </section>
</template>

<script setup lang="ts">
import type { SkillItem } from '../agentApi';
import SkillSquare from '../components/SkillSquare.vue';

const emit = defineEmits<{
  (e: 'useSkill', skill: SkillItem): void;
}>();
</script>
