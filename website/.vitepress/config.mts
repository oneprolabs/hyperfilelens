import { defineConfig } from 'vitepress'
import { zhThemeConfig } from './navigation/zh'

const enQuickStart = [
  {
    text: 'Quick start',
    items: [
      { text: 'Install HyperFileLens Community', link: '/docs/getting-started/install' },
    ],
  },
  {
    text: 'First use',
    items: [
      { text: 'Sign in to the console', link: '/docs/getting-started/sign-in' },
      { text: 'Configure external access (when needed)', link: '/docs/getting-started/configure-external-access' },
      { text: 'Add a backup source', link: '/docs/getting-started/add-source' },
      { text: 'Configure the backup source', link: '/docs/getting-started/configure-source' },
      { text: 'Add target storage', link: '/docs/getting-started/add-target' },
      { text: 'Create and run the first backup', link: '/docs/getting-started/first-backup' },
      { text: 'Check tasks and snapshots', link: '/docs/getting-started/verify-backup' },
      { text: 'Restore a test file', link: '/docs/getting-started/first-restore' },
      { text: 'Configure AI models', link: '/docs/getting-started/configure-insights-model' },
      { text: 'Create an Insights session', link: '/docs/getting-started/first-insight' },
    ],
  },
]

const enProduct = [
  {
    text: 'Product guide',
    items: [
      { text: 'Product workflow', link: '/docs/product/' },
    ],
  },
  {
    text: 'Backup and restore',
    items: [
      { text: 'Workflow', link: '/docs/backup-restore/' },
      { text: 'Manage backup sources', link: '/docs/backup-restore/sources' },
      { text: 'Manage target storage', link: '/docs/backup-restore/targets' },
      { text: 'Create and run backups', link: '/docs/backup-restore/create-backup' },
      { text: 'Policies and retention', link: '/docs/backup-restore/policies' },
      { text: 'View tasks and snapshots', link: '/docs/backup-restore/snapshots' },
      { text: 'Restore files and directories', link: '/docs/backup-restore/restore' },
    ],
  },
  {
    text: 'Insights',
    items: [
      { text: 'Workflow', link: '/docs/insights/' },
      { text: 'Prepare a snapshot', link: '/docs/insights/prepare' },
      { text: 'Create an Insights session', link: '/docs/insights/copilot' },
      { text: 'Configure AI models', link: '/docs/insights/models' },
      { text: 'Use a Private Data Gateway', link: '/docs/insights/data-gateway' },
      { text: 'Session and data scope', link: '/docs/insights/privacy' },
    ],
  },
]

const enOperations = [
  {
    text: 'Deployment and operations',
    items: [
      { text: 'Deployment guide', link: '/docs/deployment/' },
    ],
  },
  {
    text: 'Deploy HyperFileLens Community',
    items: [
      { text: 'System requirements', link: '/docs/deployment/requirements' },
      { text: 'Network and ports', link: '/docs/deployment/network' },
      { text: 'Post-installation checks', link: '/docs/deployment/post-install' },
    ],
  },
  {
    text: 'Component deployment',
    items: [
      { text: 'Deploy an Agent', link: '/docs/deployment/agent' },
      { text: 'Deploy a Proxy', link: '/docs/deployment/proxy' },
      { text: 'Deploy a Private Data Gateway', link: '/docs/deployment/data-gateway' },
    ],
  },
  {
    text: 'Operations',
    items: [
      { text: 'Jobs, alerts, and audit logs', link: '/docs/deployment/operations' },
      { text: 'Upgrade and recovery', link: '/docs/deployment/lifecycle' },
      { text: 'Uninstall Community', link: '/docs/deployment/uninstall' },
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
      { text: 'Core concepts', link: '/docs/reference/' },
      { text: 'Supported configurations', link: '/docs/reference/support-matrix' },
      { text: 'Security and limits', link: '/docs/reference/limitations-security' },
    ],
  },
  {
    text: 'Troubleshooting',
    items: [
      { text: 'Troubleshooting guide', link: '/docs/troubleshooting/' },
      { text: 'Accounts and sign-in', link: '/docs/troubleshooting/account-sign-in' },
      { text: 'Installation and nodes', link: '/docs/troubleshooting/installation-nodes' },
      { text: 'Backup, storage, and restore', link: '/docs/troubleshooting/protection' },
      { text: 'Insights and Data Gateway', link: '/docs/troubleshooting/insights' },
    ],
  },
]

export default defineConfig({
  lang: 'en-US',
  title: 'HyperFileLens',
  description: 'Open source backup with agentic AI insight — protect your files without touching production, then ask deep questions, no pre-built index required.',
  cleanUrls: true,
  rewrites: (id) => id.startsWith('en/') ? id.slice(3) : id,
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
      light: '/brand/images/hyperfilelens-lockup-transparent-on-light.png',
      dark: '/brand/images/hyperfilelens-lockup-on-dark.png',
      alt: 'HyperFileLens',
    },
    siteTitle: false,
    i18nRouting: true,
    nav: [
      {
        text: 'Quick start',
        link: '/docs/getting-started/install',
        activeMatch: '^/docs/(?:$|getting-started/)',
      },
      {
        text: 'Product guide',
        link: '/docs/product/',
        activeMatch: '^/docs/(product|backup-restore|insights)/',
      },
      { text: 'Deployment and operations', link: '/docs/deployment/' },
      {
        text: 'Help center',
        link: '/docs/help/',
        activeMatch: '^/docs/(help|reference|troubleshooting)/',
      },
    ],
    sidebar: {
      '/docs/product/': enProduct,
      '/docs/backup-restore/': enProduct,
      '/docs/insights/': enProduct,
      '/docs/deployment/': enOperations,
      '/docs/help/': enHelp,
      '/docs/reference/': enHelp,
      '/docs/troubleshooting/': enHelp,
      '/docs/': enQuickStart,
      '/docs/getting-started/': enQuickStart,
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
    root: {
      label: 'English',
      lang: 'en-US',
      link: '/',
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
