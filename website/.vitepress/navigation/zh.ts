import type { DefaultTheme } from 'vitepress'

const quickStart: DefaultTheme.SidebarItem[] = [
  {
    text: '快速开始',
    items: [
      { text: '选择使用方式', link: '/zh/docs/' },
      { text: '使用官方 SaaS', link: '/zh/docs/getting-started/saas' },
      { text: '安装社区版', link: '/zh/docs/getting-started/install' },
    ],
  },
  {
    text: '首次使用',
    items: [
      { text: '登录控制台', link: '/zh/docs/getting-started/sign-in' },
      { text: '添加备份源', link: '/zh/docs/getting-started/add-source' },
      { text: '配置备份源', link: '/zh/docs/getting-started/configure-source' },
      { text: '添加目标存储', link: '/zh/docs/getting-started/add-target' },
      { text: '创建并运行首次备份', link: '/zh/docs/getting-started/first-backup' },
      { text: '检查任务与快照', link: '/zh/docs/getting-started/verify-backup' },
      { text: '恢复测试文件', link: '/zh/docs/getting-started/first-restore' },
      { text: '创建洞察会话', link: '/zh/docs/getting-started/first-insight' },
    ],
  },
]

const product: DefaultTheme.SidebarItem[] = [
  {
    text: '产品使用',
    items: [
      { text: '业务流程', link: '/zh/docs/product/' },
    ],
  },
  {
    text: '备份与恢复',
    items: [
      { text: '使用流程', link: '/zh/docs/backup-restore/' },
      { text: '管理备份源', link: '/zh/docs/backup-restore/sources' },
      { text: '管理目标存储', link: '/zh/docs/backup-restore/targets' },
      { text: '创建并运行备份', link: '/zh/docs/backup-restore/create-backup' },
      { text: '策略与保留', link: '/zh/docs/backup-restore/policies' },
      { text: '查看任务与快照', link: '/zh/docs/backup-restore/snapshots' },
      { text: '恢复文件和目录', link: '/zh/docs/backup-restore/restore' },
    ],
  },
  {
    text: '智能洞察',
    items: [
      { text: '使用流程', link: '/zh/docs/insights/' },
      { text: '准备快照', link: '/zh/docs/insights/prepare' },
      { text: '创建洞察会话', link: '/zh/docs/insights/copilot' },
      { text: '配置 AI 模型', link: '/zh/docs/insights/models' },
      { text: '使用 Private Data Gateway', link: '/zh/docs/insights/data-gateway' },
      { text: '查看 AI 使用量', link: '/zh/docs/insights/usage' },
      { text: '会话与数据范围', link: '/zh/docs/insights/privacy' },
    ],
  },
]

const operations: DefaultTheme.SidebarItem[] = [
  {
    text: '部署运维',
    items: [
      { text: '部署指南', link: '/zh/docs/deployment/' },
    ],
  },
  {
    text: '部署社区版',
    items: [
      { text: '系统要求', link: '/zh/docs/deployment/requirements' },
      { text: '网络与端口', link: '/zh/docs/deployment/network' },
      { text: '安装后检查', link: '/zh/docs/deployment/post-install' },
    ],
  },
  {
    text: '组件部署',
    items: [
      { text: '部署 Agent', link: '/zh/docs/deployment/agent' },
      { text: '部署 Proxy', link: '/zh/docs/deployment/proxy' },
      { text: '部署 Private Data Gateway', link: '/zh/docs/deployment/data-gateway' },
    ],
  },
  {
    text: '运行维护',
    items: [
      { text: '升级与恢复', link: '/zh/docs/deployment/lifecycle' },
      { text: '任务、告警与审计', link: '/zh/docs/deployment/operations' },
    ],
  },
]

const help: DefaultTheme.SidebarItem[] = [
  {
    text: '帮助中心',
    items: [],
  },
  {
    text: '产品参考',
    items: [
      { text: '核心概念', link: '/zh/docs/reference/' },
      { text: '支持范围', link: '/zh/docs/reference/support-matrix' },
      { text: '限制与安全建议', link: '/zh/docs/reference/limitations-security' },
    ],
  },
  {
    text: '问题排查',
    items: [
      { text: '排查方法', link: '/zh/docs/troubleshooting/' },
      { text: '账户与登录', link: '/zh/docs/troubleshooting/account-sign-in' },
      { text: '安装与节点', link: '/zh/docs/troubleshooting/installation-nodes' },
      { text: '备份、存储与恢复', link: '/zh/docs/troubleshooting/protection' },
      { text: '智能洞察与 Data Gateway', link: '/zh/docs/troubleshooting/insights' },
    ],
  },
]

export const zhThemeConfig: DefaultTheme.Config = {
  logo: {
    light: '/brand/images/hyperfilelens-lockup-on-light.png',
    dark: '/brand/images/hyperfilelens-lockup-on-dark.png',
    alt: 'HyperFileLens',
  },
  logoLink: '/zh/',
  siteTitle: false,
  i18nRouting: true,
  nav: [
    {
      text: '快速开始',
      link: '/zh/docs/',
      activeMatch: '^/zh/docs/(?:$|getting-started/)',
    },
    {
      text: '产品使用',
      link: '/zh/docs/product/',
      activeMatch: '^/zh/docs/(product|backup-restore|insights)/',
    },
    { text: '部署运维', link: '/zh/docs/deployment/' },
    {
      text: '帮助中心',
      link: '/zh/docs/help/',
      activeMatch: '^/zh/docs/(help|reference|troubleshooting)/',
    },
  ],
  sidebar: {
    '/zh/docs/product/': product,
    '/zh/docs/backup-restore/': product,
    '/zh/docs/insights/': product,
    '/zh/docs/deployment/': operations,
    '/zh/docs/help/': help,
    '/zh/docs/reference/': help,
    '/zh/docs/troubleshooting/': help,
    '/zh/docs/getting-started/': quickStart,
    '/zh/docs/': quickStart,
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
      detailedView: false,
      locales: {
        zh: {
          translations: {
            button: { buttonText: '搜索文档', buttonAriaLabel: '搜索文档' },
            modal: {
              displayDetails: '显示详细列表',
              backButtonTitle: '关闭搜索',
              noResultsText: '未找到相关内容',
              resetButtonTitle: '清除查询',
              footer: {
                selectText: '选择',
                selectKeyAriaLabel: '打开结果',
                navigateText: '切换',
                navigateUpKeyAriaLabel: '上一个结果',
                navigateDownKeyAriaLabel: '下一个结果',
                closeText: '关闭',
                closeKeyAriaLabel: '关闭搜索',
              },
            },
          },
        },
      },
    },
  },
}
