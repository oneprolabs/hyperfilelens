<script setup lang="ts">
import ModulePage from '../../../components/ModulePage.vue'
import AccountProfile from '../../../pages/account/AccountProfile.vue'
import { useResolvedPlatformOpsSideNav } from '../../composables/useResolvedPlatformOpsSideNav'

const menus = useResolvedPlatformOpsSideNav()
</script>

<template>
  <ModulePage :menus="menus">
    <AccountProfile :show-organization-role="false" />
  </ModulePage>
</template>
