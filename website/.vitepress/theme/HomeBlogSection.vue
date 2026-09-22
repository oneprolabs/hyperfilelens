<script setup lang="ts">
import { computed } from 'vue'
import BlogCard from './BlogCard.vue'
import { blogCopy } from './blogCopy'
import { buildFeed, type ExternalEntry } from './blog'
import type { HomeLocale } from './homeCopy'
import { data as posts } from '../data/posts.data'
import externalEn from '../../en/blog/external.json'
import externalZh from '../../zh/blog/external.json'
import externalEs from '../../es/blog/external.json'

const props = defineProps<{ locale: HomeLocale }>()

const externalByLocale = {
  en: externalEn as ExternalEntry[],
  zh: externalZh as ExternalEntry[],
  es: externalEs as ExternalEntry[],
}

const copy = computed(() => blogCopy[props.locale])
const blogRoot = computed(() => (props.locale === 'en' ? '/blog/' : `/${props.locale}/blog/`))
const latest = computed(() => buildFeed(posts, externalByLocale[props.locale], props.locale).slice(0, 3))
</script>

<template>
  <section v-if="latest.length" id="blog" class="section-block home-blog" aria-labelledby="home-blog-title">
    <div class="section-heading centered">
      <p class="section-kicker">{{ copy.homeKicker }}</p>
      <h2 id="home-blog-title">{{ copy.homeTitle }}</h2>
    </div>
    <div class="blog-grid">
      <BlogCard v-for="entry in latest" :key="entry.url" :entry="entry" :locale="locale" />
    </div>
    <p class="home-blog-more">
      <a class="text-link" :href="blogRoot">{{ copy.homeViewAll }} <svg aria-hidden="true"><use href="#icon-arrow" /></svg></a>
    </p>
  </section>
</template>
