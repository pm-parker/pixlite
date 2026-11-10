<script setup>
import { ref, computed } from 'vue'
import HomePage from './views/HomePage.vue'
import Workspace from './views/Workspace.vue'

const route = ref({ name: 'home' }) // {name:'home'} | {name:'work', projectId}

function openProject(id) {
  route.value = { name: 'work', projectId: id }
}
function goHome() {
  route.value = { name: 'home' }
}
</script>

<template>
  <HomePage v-if="route.name === 'home'" @open="openProject" />
  <Workspace v-else-if="route.name === 'work'" :project-id="route.projectId" @back="goHome" />
</template>

<style>
</style>
