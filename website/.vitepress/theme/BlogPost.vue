<script setup lang="ts">
import { computed } from 'vue'
import { useData } from 'vitepress'
import IconSprite from './IconSprite.vue'
import SiteHeader from './SiteHeader.vue'
import SiteFooter from './SiteFooter.vue'
import { blogCopy } from './blogCopy'
import { formatDate } from './blog'
import { useSiteLocale } from './useSiteLocale'
import { homeCopy } from './homeCopy'

const { frontmatter } = useData()
const locale = useSiteLocale()
const copy = computed(() => blogCopy[locale.value])
const home = computed(() => homeCopy[locale.value])

const blogRoot = computed(() => (locale.value === 'en' ? '/blog/' : `/${locale.value}/blog/`))

function toIso(value: unknown): string {
  if (value instanceof Date) return value.toISOString().slice(0, 10)
  return typeof value === 'string' ? value.slice(0, 10) : ''
}

const date = computed(() => toIso(frontmatter.value.date))
const meta = computed(() =>
  [frontmatter.value.author as string | undefined, date.value ? formatDate(date.value, locale.value) : '']
    .filter(Boolean),
)
</script>

<template>
  <div class="hfl-site">
    <IconSprite />
    <SiteHeader :locale="locale" :anchor-base="home.home" />

    <main>
      <article class="section-block blog-post">
        <a class="text-link blog-back" :href="blogRoot">
          <svg aria-hidden="true" class="blog-back-icon"><use href="#icon-arrow" /></svg>
          {{ copy.backToBlog }}
        </a>
        <h1>{{ frontmatter.title }}</h1>
        <p v-if="meta.length" class="blog-post-meta">
          <span v-for="(item, index) in meta" :key="item">
            <span v-if="index > 0" class="blog-meta-sep" aria-hidden="true">·</span>{{ item }}
          </span>
        </p>
        <p v-if="frontmatter.description" class="blog-post-lead">{{ frontmatter.description }}</p>

        <div class="blog-post-body vp-doc">
          <Content />
        </div>

        <p v-if="frontmatter.canonical" class="blog-post-canonical">
          {{ copy.publishedOn }}
          <a :href="frontmatter.canonical" target="_blank" rel="noopener noreferrer">{{ frontmatter.canonicalSource || frontmatter.canonical }}</a>
        </p>
      </article>
    </main>

    <SiteFooter :locale="locale" :anchor-base="home.home" />
  </div>
</template>
