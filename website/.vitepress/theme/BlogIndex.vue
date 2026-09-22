<script setup lang="ts">
import { computed } from 'vue'
import IconSprite from './IconSprite.vue'
import SiteHeader from './SiteHeader.vue'
import SiteFooter from './SiteFooter.vue'
import BlogCard from './BlogCard.vue'
import { blogCopy } from './blogCopy'
import { buildFeed, type ExternalEntry } from './blog'
import { useSiteLocale } from './useSiteLocale'
import { homeCopy } from './homeCopy'
import { data as posts } from '../data/posts.data'
import externalEn from '../../en/blog/external.json'
import externalZh from '../../zh/blog/external.json'
import externalEs from '../../es/blog/external.json'

// One curated list per locale: the outside articles worth showing a Spanish
// reader are not the same ones worth showing a Chinese reader.
const externalByLocale = {
  en: externalEn as ExternalEntry[],
  zh: externalZh as ExternalEntry[],
  es: externalEs as ExternalEntry[],
}

const locale = useSiteLocale()
const copy = computed(() => blogCopy[locale.value])
const home = computed(() => homeCopy[locale.value])

const feed = computed(() => buildFeed(posts, externalByLocale[locale.value], locale.value))

</script>

<template>
  <div class="hfl-site">
    <IconSprite />
    <SiteHeader :locale="locale" :anchor-base="home.home" />

    <main>
      <section class="section-block blog-index" aria-labelledby="blog-title">
        <div class="section-heading">
          <p class="section-kicker">{{ copy.kicker }}</p>
          <h1 id="blog-title">{{ copy.title }}</h1>
          <p class="blog-lead">{{ copy.lead }}</p>
        </div>


        <div v-if="feed.length" class="blog-grid">
          <BlogCard v-for="entry in feed" :key="entry.url" :entry="entry" :locale="locale" />
        </div>
        <p v-else class="blog-empty">{{ copy.empty }}</p>
      </section>
    </main>

    <SiteFooter :locale="locale" :anchor-base="home.home" />
  </div>
</template>
