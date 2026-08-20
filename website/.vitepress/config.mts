import { defineConfig } from 'vitepress'

export default defineConfig({
  lang: 'en-US',
  title: 'HyperFileLens',
  description: 'Open source backup with agentic AI insight — protect your files without touching production, then ask deep questions, no pre-built index required.',
  cleanUrls: true,
  head: [
    ['link', { rel: 'icon', type: 'image/svg+xml', href: '/logo-mark.svg' }],
    ['meta', { name: 'theme-color', content: '#07111f' }],
    ['meta', { name: 'viewport', content: 'width=device-width, initial-scale=1' }],
    ['meta', { property: 'og:type', content: 'website' }],
    ['meta', { property: 'og:title', content: 'HyperFileLens — Your backups know more than you think.' }],
    ['meta', { property: 'og:description', content: 'Open source backup with agentic AI insight — protect your files without touching production, then ask deep questions, no pre-built index required.' }],
    ['meta', { name: 'twitter:card', content: 'summary' }],
    ['script', { src: '/website-runtime-config.js' }],
  ],
  themeConfig: {
    logo: '/logo-mark.svg',
    siteTitle: 'HyperFileLens',
  },
  locales: {
    en: {
      label: 'English',
      lang: 'en-US',
      link: '/en/',
      title: 'HyperFileLens',
      description: 'Open source backup with agentic AI insight — protect your files without touching production, then ask deep questions, no pre-built index required.',
      head: [
        ['meta', { property: 'og:title', content: 'HyperFileLens — Your backups know more than you think.' }],
        ['meta', { property: 'og:description', content: 'Open source backup with agentic AI insight — protect your files without touching production, then ask deep questions, no pre-built index required.' }],
      ],
    },
    zh: {
      label: '简体中文',
      lang: 'zh-Hans',
      link: '/zh/',
      title: 'HyperFileLens',
      description: '开源备份工具，内置 Agentic AI 洞察能力——在不影响生产环境的前提下保护你的文件，再对备份直接提问，无需预建索引。',
      head: [
        ['meta', { property: 'og:title', content: 'HyperFileLens — 你的备份，藏着意想不到的答案。' }],
        ['meta', { property: 'og:description', content: '开源备份工具，内置 Agentic AI 洞察能力——在不影响生产环境的前提下保护你的文件，再对备份直接提问，无需预建索引。' }],
      ],
    },
  },
})
