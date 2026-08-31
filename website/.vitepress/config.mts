import { defineConfig } from 'vitepress'
import { zhThemeConfig } from './navigation/zh'

const enQuickStart = [
  {
    text: 'Quick Start',
    items: [
      { text: 'Getting Started', link: '/en/docs/' },
      { text: 'Use HyperFileLens SaaS', link: '/en/docs/getting-started/saas' },
      { text: 'Install Community', link: '/en/docs/getting-started/install' },
    ],
  },
  {
    text: 'First Use',
    items: [
      { text: 'Sign in to the Console', link: '/en/docs/getting-started/sign-in' },
      { text: 'Add a Backup Source', link: '/en/docs/getting-started/add-source' },
      { text: 'Configure the Backup Source', link: '/en/docs/getting-started/configure-source' },
      { text: 'Add Target Storage', link: '/en/docs/getting-started/add-target' },
      { text: 'Create and Run the First Backup', link: '/en/docs/getting-started/first-backup' },
      { text: 'Check Tasks and Snapshots', link: '/en/docs/getting-started/verify-backup' },
      { text: 'Restore a Test File', link: '/en/docs/getting-started/first-restore' },
      { text: 'Create an Insights Session', link: '/en/docs/getting-started/first-insight' },
    ],
  },
]

const enProduct = [
  {
    text: 'Product Usage',
    items: [
      { text: 'Product Workflow', link: '/en/docs/product/' },
    ],
  },
  {
    text: 'Backup & Restore',
    items: [
      { text: 'Usage Flow', link: '/en/docs/backup-restore/' },
      { text: 'Manage Backup Sources', link: '/en/docs/backup-restore/sources' },
      { text: 'Manage Target Storage', link: '/en/docs/backup-restore/targets' },
      { text: 'Create and Run Backups', link: '/en/docs/backup-restore/create-backup' },
      { text: 'Policies and Retention', link: '/en/docs/backup-restore/policies' },
      { text: 'View Tasks and Snapshots', link: '/en/docs/backup-restore/snapshots' },
      { text: 'Restore Files and Directories', link: '/en/docs/backup-restore/restore' },
    ],
  },
]

const enOperations = [
  {
    text: 'Deployment & Operations',
    items: [
      { text: 'Deployment Guide', link: '/en/docs/deployment/' },
    ],
  },
  {
    text: 'Deploy Community',
    items: [
      { text: 'System Requirements', link: '/en/docs/deployment/requirements' },
      { text: 'Network and Ports', link: '/en/docs/deployment/network' },
      { text: 'Post-installation Checks', link: '/en/docs/deployment/post-install' },
    ],
  },
  {
    text: 'Component Deployment',
    items: [
      { text: 'Deploy an Agent', link: '/en/docs/deployment/agent' },
      { text: 'Deploy a Proxy', link: '/en/docs/deployment/proxy' },
      { text: 'Deploy a Private Data Gateway', link: '/en/docs/deployment/data-gateway' },
    ],
  },
  {
    text: 'Operations',
    items: [
      { text: 'Upgrade and Recovery', link: '/en/docs/deployment/lifecycle' },
      { text: 'Jobs, Alerts, and Audit Logs', link: '/en/docs/deployment/operations' },
    ],
  },
]

const enHelp = [
  {
    text: 'Help Center',
    items: [],
  },
  {
    text: 'Product Reference',
    items: [
      { text: 'Core Concepts', link: '/en/docs/reference/' },
      { text: 'Supported Configurations', link: '/en/docs/reference/support-matrix' },
      { text: 'Security & Limits', link: '/en/docs/reference/limitations-security' },
    ],
  },
  {
    text: 'Troubleshooting',
    items: [
      { text: 'Troubleshooting Guide', link: '/en/docs/troubleshooting/' },
      { text: 'Accounts and Sign-in', link: '/en/docs/troubleshooting/account-sign-in' },
      { text: 'Installation and Nodes', link: '/en/docs/troubleshooting/installation-nodes' },
      { text: 'Backup, Storage, and Restore', link: '/en/docs/troubleshooting/protection' },
      { text: 'Insights and Data Gateway', link: '/en/docs/troubleshooting/insights' },
    ],
  },
]

export default defineConfig({
  lang: 'en-US',
  title: 'HyperFileLens',
  description: 'Open source backup with agentic AI insight — protect your files without touching production, then ask deep questions, no pre-built index required.',
  cleanUrls: true,
  head: [
    ['link', { rel: 'icon', type: 'image/x-icon', href: '/brand/icons/favicon.ico' }],
    ['meta', { name: 'theme-color', content: '#07111f' }],
    ['meta', { name: 'viewport', content: 'width=device-width, initial-scale=1' }],
    ['meta', { property: 'og:type', content: 'website' }],
    ['meta', { property: 'og:title', content: 'HyperFileLens — Your backups know more than you think.' }],
    ['meta', { property: 'og:description', content: 'Open source backup with agentic AI insight — protect your files without touching production, then ask deep questions, no pre-built index required.' }],
    ['meta', { name: 'twitter:card', content: 'summary' }],
    ['script', { src: '/website-runtime-config.js' }],
  ],
  themeConfig: {
    logo: {
      light: '/brand/images/hyperfilelens-lockup-on-light.png',
      dark: '/brand/images/hyperfilelens-lockup-on-dark.png',
      alt: 'HyperFileLens',
    },
    siteTitle: false,
    i18nRouting: true,
    nav: [
      {
        text: 'Quick Start',
        link: '/en/docs/',
        activeMatch: '^/en/docs/(?:$|getting-started/)',
      },
      {
        text: 'Product Usage',
        link: '/en/docs/product/',
        activeMatch: '^/en/docs/(product|backup-restore|insights)/',
      },
      { text: 'Deployment & Operations', link: '/en/docs/deployment/' },
      {
        text: 'Help Center',
        link: '/en/docs/help/',
        activeMatch: '^/en/docs/(help|reference|troubleshooting)/',
      },
    ],
    sidebar: {
      '/en/docs/product/': enProduct,
      '/en/docs/backup-restore/': enProduct,
      '/en/docs/insights/': enProduct,
      '/en/docs/deployment/': enOperations,
      '/en/docs/help/': enHelp,
      '/en/docs/reference/': enHelp,
      '/en/docs/troubleshooting/': enHelp,
      '/en/docs/': enQuickStart,
      '/en/docs/getting-started/': enQuickStart,
    },
    socialLinks: [
      { icon: 'github', link: 'https://github.com/oneprolabs/hyperfilelens' },
    ],
    search: {
      provider: 'local',
      options: {
        locales: {
          zh: {
            translations: {
              button: { buttonText: '搜索文档', buttonAriaLabel: '搜索文档' },
              modal: {
                noResultsText: '未找到相关内容',
                resetButtonTitle: '清除查询',
                footer: { selectText: '选择', navigateText: '切换', closeText: '关闭' },
              },
            },
          },
        },
      },
    },
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
      themeConfig: zhThemeConfig,
      head: [
        ['meta', { property: 'og:title', content: 'HyperFileLens — 你的备份，藏着意想不到的答案。' }],
        ['meta', { property: 'og:description', content: '开源备份工具，内置 Agentic AI 洞察能力——在不影响生产环境的前提下保护你的文件，再对备份直接提问，无需预建索引。' }],
      ],
    },
  },
})
