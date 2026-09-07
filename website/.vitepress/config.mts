import { defineConfig } from 'vitepress'
import { zhThemeConfig } from './navigation/zh'

const enQuickStart = [
  {
    text: 'Quick start',
    items: [
      { text: 'Install HyperFileLens Community', link: '/en/docs/getting-started/install' },
    ],
  },
  {
    text: 'First use',
    items: [
      { text: 'Sign in to the console', link: '/en/docs/getting-started/sign-in' },
      { text: 'Add a backup source', link: '/en/docs/getting-started/add-source' },
      { text: 'Configure the backup source', link: '/en/docs/getting-started/configure-source' },
      { text: 'Add target storage', link: '/en/docs/getting-started/add-target' },
      { text: 'Create and run the first backup', link: '/en/docs/getting-started/first-backup' },
      { text: 'Check tasks and snapshots', link: '/en/docs/getting-started/verify-backup' },
      { text: 'Restore a test file', link: '/en/docs/getting-started/first-restore' },
      { text: 'Configure an AI model for Insights', link: '/en/docs/getting-started/configure-insights-model' },
      { text: 'Create an Insights session', link: '/en/docs/getting-started/first-insight' },
    ],
  },
]

const enProduct = [
  {
    text: 'Product guide',
    items: [
      { text: 'Product workflow', link: '/en/docs/product/' },
    ],
  },
  {
    text: 'Backup and restore',
    items: [
      { text: 'Workflow', link: '/en/docs/backup-restore/' },
      { text: 'Manage backup sources', link: '/en/docs/backup-restore/sources' },
      { text: 'Manage target storage', link: '/en/docs/backup-restore/targets' },
      { text: 'Create and run backups', link: '/en/docs/backup-restore/create-backup' },
      { text: 'Policies and retention', link: '/en/docs/backup-restore/policies' },
      { text: 'View tasks and snapshots', link: '/en/docs/backup-restore/snapshots' },
      { text: 'Restore files and directories', link: '/en/docs/backup-restore/restore' },
    ],
  },
  {
    text: 'Insights',
    items: [
      { text: 'Workflow', link: '/en/docs/insights/' },
      { text: 'Prepare a snapshot', link: '/en/docs/insights/prepare' },
      { text: 'Create an Insights session', link: '/en/docs/insights/copilot' },
      { text: 'Configure AI models', link: '/en/docs/insights/models' },
      { text: 'Use a Private Data Gateway', link: '/en/docs/insights/data-gateway' },
      { text: 'Session and data scope', link: '/en/docs/insights/privacy' },
    ],
  },
]

const enOperations = [
  {
    text: 'Deployment and operations',
    items: [
      { text: 'Deployment guide', link: '/en/docs/deployment/' },
    ],
  },
  {
    text: 'Deploy HyperFileLens Community',
    items: [
      { text: 'System requirements', link: '/en/docs/deployment/requirements' },
      { text: 'Network and ports', link: '/en/docs/deployment/network' },
      { text: 'Post-installation checks', link: '/en/docs/deployment/post-install' },
    ],
  },
  {
    text: 'Component deployment',
    items: [
      { text: 'Deploy an Agent', link: '/en/docs/deployment/agent' },
      { text: 'Deploy a Proxy', link: '/en/docs/deployment/proxy' },
      { text: 'Deploy a Private Data Gateway', link: '/en/docs/deployment/data-gateway' },
    ],
  },
  {
    text: 'Operations',
    items: [
      { text: 'Upgrade and recovery', link: '/en/docs/deployment/lifecycle' },
      { text: 'Jobs, alerts, and audit logs', link: '/en/docs/deployment/operations' },
    ],
  },
]

const enHelp = [
  {
    text: 'Help center',
    items: [],
  },
  {
    text: 'Product reference',
    items: [
      { text: 'Core concepts', link: '/en/docs/reference/' },
      { text: 'Supported configurations', link: '/en/docs/reference/support-matrix' },
      { text: 'Security and limits', link: '/en/docs/reference/limitations-security' },
    ],
  },
  {
    text: 'Troubleshooting',
    items: [
      { text: 'Troubleshooting guide', link: '/en/docs/troubleshooting/' },
      { text: 'Accounts and sign-in', link: '/en/docs/troubleshooting/account-sign-in' },
      { text: 'Installation and nodes', link: '/en/docs/troubleshooting/installation-nodes' },
      { text: 'Backup, storage, and restore', link: '/en/docs/troubleshooting/protection' },
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
        text: 'Quick start',
        link: '/en/docs/getting-started/install',
        activeMatch: '^/en/docs/(?:$|getting-started/)',
      },
      {
        text: 'Product guide',
        link: '/en/docs/product/',
        activeMatch: '^/en/docs/(product|backup-restore|insights)/',
      },
      { text: 'Deployment and operations', link: '/en/docs/deployment/' },
      {
        text: 'Help center',
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
