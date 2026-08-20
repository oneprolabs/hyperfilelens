import type { DefaultTheme } from 'vitepress'

const overview: DefaultTheme.SidebarItem[] = [
  {
    text: '文档概览',
    items: [
      { text: '用户文档', link: '/zh/docs/' },
      { text: '开始使用', link: '/zh/docs/getting-started/' },
      { text: '部署与节点', link: '/zh/docs/deployment/' },
      { text: '备份与恢复', link: '/zh/docs/backup-restore/' },
      { text: '智能洞察', link: '/zh/docs/insights/' },
      { text: '概念与能力', link: '/zh/docs/reference/' },
      { text: '常见问题与排障', link: '/zh/docs/troubleshooting/' },
    ],
  },
]

const gettingStarted: DefaultTheme.SidebarItem[] = [
  {
    text: '开始使用',
    items: [
      { text: '概览', link: '/zh/docs/getting-started/' },
      { text: '部署要求', link: '/zh/docs/getting-started/requirements' },
      { text: '安装 HyperFileLens', link: '/zh/docs/getting-started/install' },
      { text: '完成首次备份', link: '/zh/docs/getting-started/first-backup' },
    ],
  },
  {
    text: '下一步',
    items: [
      { text: '备份与恢复', link: '/zh/docs/backup-restore/' },
      { text: '智能洞察', link: '/zh/docs/insights/' },
    ],
  },
]

const deployment: DefaultTheme.SidebarItem[] = [
  {
    text: '部署与节点',
    items: [
      { text: '概览', link: '/zh/docs/deployment/' },
      { text: 'Agent 与 Proxy', link: '/zh/docs/deployment/nodes' },
      { text: 'Data Gateway', link: '/zh/docs/deployment/data-gateway' },
      { text: '升级、备份与回退', link: '/zh/docs/deployment/lifecycle' },
    ],
  },
  {
    text: '相关参考',
    items: [
      { text: '支持范围', link: '/zh/docs/reference/support-matrix' },
      { text: '安装与节点排障', link: '/zh/docs/troubleshooting/installation-nodes' },
    ],
  },
]

const protection: DefaultTheme.SidebarItem[] = [
  {
    text: '备份与恢复',
    items: [
      { text: '概览', link: '/zh/docs/backup-restore/' },
      { text: '准备备份源和目标存储', link: '/zh/docs/backup-restore/prepare' },
      { text: '创建并运行备份', link: '/zh/docs/backup-restore/create-backup' },
      { text: '查看任务与验证快照', link: '/zh/docs/backup-restore/snapshots' },
      { text: '恢复文件和目录', link: '/zh/docs/backup-restore/restore' },
      { text: '策略、保留与恢复计划', link: '/zh/docs/backup-restore/policies' },
    ],
  },
  {
    text: '故障排查',
    items: [
      { text: '备份、存储与恢复', link: '/zh/docs/troubleshooting/protection' },
    ],
  },
]

const insights: DefaultTheme.SidebarItem[] = [
  {
    text: '智能洞察',
    items: [
      { text: '概览', link: '/zh/docs/insights/' },
      { text: '准备快照和 Data Gateway', link: '/zh/docs/insights/prepare' },
      { text: '创建和使用 Copilot 会话', link: '/zh/docs/insights/copilot' },
      { text: '会话、引用与数据边界', link: '/zh/docs/insights/privacy' },
    ],
  },
  {
    text: '故障排查',
    items: [
      { text: 'Data Gateway 与 Copilot', link: '/zh/docs/troubleshooting/insights' },
    ],
  },
]

const reference: DefaultTheme.SidebarItem[] = [
  {
    text: '概念与能力',
    items: [
      { text: '核心概念', link: '/zh/docs/reference/' },
      { text: '支持范围', link: '/zh/docs/reference/support-matrix' },
      { text: '限制与安全建议', link: '/zh/docs/reference/limitations-security' },
    ],
  },
  {
    text: '常见问题与排障',
    items: [
      { text: '排障入口', link: '/zh/docs/troubleshooting/' },
      { text: '安装与节点', link: '/zh/docs/troubleshooting/installation-nodes' },
      { text: '备份、存储与恢复', link: '/zh/docs/troubleshooting/protection' },
      { text: 'Data Gateway 与 Copilot', link: '/zh/docs/troubleshooting/insights' },
      { text: '账户与登录', link: '/zh/docs/troubleshooting/account-sign-in' },
    ],
  },
]

export const zhThemeConfig: DefaultTheme.Config = {
  logo: '/logo-mark.svg',
  logoLink: '/zh/',
  siteTitle: 'HyperFileLens',
  i18nRouting: false,
  nav: [
    { text: '开始使用', link: '/zh/docs/getting-started/' },
    { text: '部署', link: '/zh/docs/deployment/' },
    { text: '备份与恢复', link: '/zh/docs/backup-restore/' },
    { text: '智能洞察', link: '/zh/docs/insights/' },
    {
      text: '参考与排障',
      link: '/zh/docs/reference/',
      activeMatch: '^/zh/docs/(reference|troubleshooting)/',
    },
  ],
  sidebar: {
    '/zh/docs/getting-started/': gettingStarted,
    '/zh/docs/deployment/': deployment,
    '/zh/docs/backup-restore/': protection,
    '/zh/docs/insights/': insights,
    '/zh/docs/reference/': reference,
    '/zh/docs/troubleshooting/': reference,
    '/zh/docs/': overview,
  },
  outline: { label: '本页内容', level: [2, 3] },
  docFooter: { prev: '上一页', next: '下一页' },
  returnToTopLabel: '返回顶部',
  sidebarMenuLabel: '目录',
  langMenuLabel: '切换语言',
  skipToContentLabel: '跳到正文',
  darkModeSwitchLabel: '外观',
  lightModeSwitchTitle: '切换为浅色模式',
  darkModeSwitchTitle: '切换为深色模式',
  lastUpdated: { text: '最后更新', formatOptions: { dateStyle: 'medium' } },
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
}
