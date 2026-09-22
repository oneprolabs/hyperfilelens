// Landing page copy, one entry per locale.
//
// The landing markup lives in a single HomeLanding.vue. Everything that differs
// between locales is text, so it belongs here — adding a language means adding a
// block below, not copying the component.

export interface HomeNavItem {
  label: string
  href: string
  /** External routes open in a new tab; in-page anchors do not. */
  external?: boolean
}

export interface HomeFooterLink {
  label: string
  /**
   * Either a literal path/mailto, or a token resolved at render time:
   * '@app' (console login), '@github', '@sourcelens', '@releases', '@issues'.
   */
  href: string
  external?: boolean
}

export interface HomeCardCopy {
  title: string
  body: string
}

export interface HomeCopy {
  /** BCP-47 tag used for the <html lang> attribute of the landing. */
  lang: string
  /** Locale root, used by the brand/footer logo links. */
  home: string
  nav: HomeNavItem[]
  header: {
    brandAria: string
    navAria: string
    githubAria: string
    githubLabel: string
    cta: string
    openMenu: string
    closeMenu: string
    mobileNavAria: string
  }
  hero: {
    pill: string
    titleLead: string
    titleAccent: string
    lead: string
    primaryCta: string
    freeBadge: string
    secondaryCta: string
    proofAria: string
    proof: string[]
    stageAria: string
    shotAlt: string
  }
  useCases: {
    kicker: string
    title: string
    cards: HomeCardCopy[]
    divider: string
    moreCards: HomeCardCopy[]
  }
  howItWorks: {
    kicker: string
    title: string
    lead: string
    diagramAlt: string
  }
  openSource: {
    kicker: string
    titleLines: string[]
    lead: string
    callout: string
    githubCta: string
    starCta: string
    engineRepo: string
    installGuide: string
    installGuideHref: string
    betaNote: string
    terminal: {
      aria: string
      title: string
      subtitle: string
      copy: string
      copied: string
      context: string
      comment: string
      installCommand: string
      summaryTitle: string
      summarySteps: string
    }
  }
  contact: {
    kicker: string
    title: string
    enterprise: HomeCardCopy & { linkLabel: string }
    support: HomeCardCopy & { linkLabel: string }
  }
  footer: {
    blurb: string
    socialAria: string
    columns: { title: string; links: HomeFooterLink[] }[]
    bottom: [string, string]
  }
}

const en: HomeCopy = {
  lang: 'en-US',
  home: '/',
  nav: [
    { label: 'Use Cases', href: '#use-cases' },
    { label: 'How It Works', href: '#how-it-works' },
    { label: 'Documentation', href: '/docs/getting-started/install', external: true },
    { label: 'Blog', href: '/blog/' },
    { label: 'Open Source', href: '#open-source' },
    { label: 'Contact', href: '#contact' },
  ],
  header: {
    brandAria: 'HyperFileLens home',
    navAria: 'Main navigation',
    githubAria: 'HyperFileLens on GitHub',
    githubLabel: 'GitHub',
    cta: 'Try free',
    openMenu: 'Open menu',
    closeMenu: 'Close menu',
    mobileNavAria: 'Mobile navigation',
  },
  hero: {
    pill: 'OneProLabs · Apache 2.0',
    titleLead: 'Your backups know',
    titleAccent: 'more than you think.',
    lead: 'Ask questions straight from your document backups — PDFs, Word, Excel, PowerPoint, images, Markdown, or any other text format — without touching production.',
    primaryCta: 'Try HyperFileLens',
    freeBadge: 'Free',
    secondaryCta: 'View on GitHub',
    proofAria: 'Key product qualities',
    proof: ['Data Governance', 'Agentic RAG', 'Agent Harness', 'Never Disrupts Production'],
    stageAria: 'HyperFileLens control plane preview',
    shotAlt: 'HyperFileLens Overview dashboard showing the data protection pipeline from production source to isolated target storage to recovery drill, with 452 sources and 428.7 TB protected, all systems healthy, and recovery verification ready.',
  },
  useCases: {
    kicker: 'Use Cases',
    title: 'Answers, not just backup search results.',
    cards: [
      {
        title: 'Customer support Q&A',
        body: 'Answer customer questions straight from your product docs, PDFs, and slide decks — multimodal understanding reads across formats, so nothing needs to be reformatted first.',
      },
      {
        title: 'Company knowledge base',
        body: 'Turn scattered specs, policies, and decisions — engineering or company-wide — into one knowledge base every team can actually query, not just search and summarize.',
      },
      {
        title: 'Source-grounded root cause analysis',
        body: 'Not keyword matching, not log scraping — the agent reads and reasons through your real source code, the way a senior engineer would, until it can confirm the actual root cause.',
      },
    ],
    divider: 'Still a rock-solid backup tool',
    moreCards: [
      {
        title: 'Any host, plus NAS',
        body: 'Windows, Linux, and Mac — servers and workstations alike — plus NAS shares, all protected under one policy, not three different tools.',
      },
      {
        title: 'No storage lock-in',
        body: "Object storage or local storage, any S3-compatible provider — your backups aren't tied to one storage vendor.",
      },
      {
        title: 'Single-file recovery',
        body: 'Need one file back right now? Browse any snapshot and restore just that file in seconds — no full-volume restore required.',
      },
    ],
  },
  howItWorks: {
    kicker: 'How It Works',
    title: 'Your files stay put. The engine comes to them.',
    lead: 'HyperFileLens backs up your documents and code into a safe, isolated copy — then SourceLens, our open source Agentic RAG engine, reasons directly over that copy, never touching production.',
    diagramAlt: 'Your documents (Word, PDF, Excel, PowerPoint, images, code, markdown) flow into HyperFileLens for open source backup, protection, and flexible storage. HyperFileLens produces an isolated safe copy with no impact on production, which the SourceLens open source AI agent reasons over directly — reading, searching, navigating, and reasoning with no pre-built index — to produce AI insights: customer support Q&A, a company knowledge base, source-grounded root cause analysis, and actionable insights. The whole pipeline is governed and secure: access control, data stays in your control, audit and compliance, enterprise ready. Open source, Apache 2.0.',
  },
  openSource: {
    kicker: 'Community',
    titleLines: ['Open source.', 'One-command install.'],
    lead: 'Install the latest Community tag on an Ubuntu host with Docker. The installer verifies its images and assets before changing the host.',
    callout: 'Community is free and open source, with S3-compatible storage and an AI model or API key. Enterprise capabilities will be available in a later release.',
    githubCta: 'View on GitHub',
    starCta: '⭐ Star this project',
    engineRepo: 'AI engine repo',
    installGuide: 'Installation guide',
    installGuideHref: '/docs/getting-started/install',
    betaNote: 'HyperFileLens is currently in public beta.',
    terminal: {
      aria: 'Community online installation command',
      title: 'Community',
      subtitle: 'Latest tag',
      copy: 'Copy command',
      copied: 'Copied',
      context: 'Run on an Ubuntu host',
      comment: '# Install the latest Community tag',
      installCommand: [
        'curl -fsSL \\',
        '  https://raw.githubusercontent.com/oneprolabs/hyperfilelens/main/deploy/online/install.sh \\',
        '  | sudo bash -s -- --mirror global --yes',
      ].join('\n'),
      summaryTitle: 'Installer handles the rest',
      summarySteps: 'Environment check · Image pull · Service startup',
    },
  },
  contact: {
    kicker: 'Contact',
    title: 'Contact us',
    enterprise: {
      title: 'Enterprise deployment',
      body: 'Planning a private, self-hosted deployment for your organization? We can help you scope it.',
      linkLabel: 'Email us',
    },
    support: {
      title: 'Technical support',
      body: "Running into an issue or found a bug? File it on GitHub and we'll take it from there.",
      linkLabel: 'Open an issue',
    },
  },
  footer: {
    blurb: 'Open source backup with agentic AI insight, by OneProLabs.',
    socialAria: 'HyperFileLens on GitHub',
    columns: [
      {
        title: 'Product',
        links: [
          { label: 'Use Cases', href: '#use-cases' },
          { label: 'How It Works', href: '#how-it-works' },
          { label: 'Documentation', href: '/docs/getting-started/install', external: true },
          { label: 'Blog', href: '/blog/' },
          { label: 'Try free', href: '@app', external: true },
        ],
      },
      {
        title: 'Open Source',
        links: [
          { label: 'HyperFileLens', href: '@github', external: true },
          { label: 'AI engine', href: '@sourcelens', external: true },
          { label: 'Releases', href: '@releases', external: true },
        ],
      },
      {
        title: 'Contact',
        links: [
          { label: 'Enterprise deployment', href: 'mailto:oneprolabs@oneprocloud.com' },
          { label: 'Technical support', href: '@issues', external: true },
        ],
      },
    ],
    bottom: ['© 2026 OneProLabs', 'Public beta · Built in the open'],
  },
}

const zh: HomeCopy = {
  lang: 'zh-Hans',
  home: '/zh/',
  nav: [
    { label: '使用场景', href: '#use-cases' },
    { label: '工作原理', href: '#how-it-works' },
    { label: '文档', href: '/zh/docs/getting-started/install', external: true },
    { label: '博客', href: '/zh/blog/' },
    { label: '开源', href: '#open-source' },
    { label: '联系我们', href: '#contact' },
  ],
  header: {
    brandAria: 'HyperFileLens 首页',
    navAria: '主导航',
    githubAria: 'HyperFileLens GitHub 仓库',
    githubLabel: 'GitHub',
    cta: '免费试用',
    openMenu: '打开菜单',
    closeMenu: '关闭菜单',
    mobileNavAria: '移动导航',
  },
  hero: {
    pill: 'OneProLabs · Apache 2.0',
    titleLead: '你的备份',
    titleAccent: '藏着意想不到的答案',
    lead: '直接对你的文档备份进行问答——PDF、Word、Excel、PPT、图片、Markdown 等任意文本格式，且不影响生产环境。',
    primaryCta: '试用 HyperFileLens',
    freeBadge: '免费',
    secondaryCta: '查看 GitHub 仓库',
    proofAria: '核心特性',
    proof: ['数据治理', 'Agentic RAG', 'Agent Harness', '不影响生产环境'],
    stageAria: 'HyperFileLens 控制台预览',
    shotAlt: 'HyperFileLens 概览仪表盘：数据保护流水线从生产源到隔离的目标存储，再到恢复演练，已保护 452 个源、428.7 TB 数据，系统状态健康，恢复验证就绪。',
  },
  useCases: {
    kicker: '使用场景',
    title: '不止是检索 更是答案',
    cards: [
      {
        title: '智能客服问答',
        body: '直接基于产品文档、PDF、PPT 回答客户问题——多模态识别跨格式读取，不用预先转换格式。',
      },
      {
        title: '企业知识库',
        body: '把分散的技术文档、管理制度、决策记录——不管是研发的还是全公司的——整合成一个团队真正能查询的知识库，而不只是搜索和摘要。',
      },
      {
        title: '基于源码的根因分析',
        body: '不是关键词匹配，也不是扫日志——agent 会像资深工程师一样真正阅读、推理你的源代码，直到确认真正的根因。',
      },
    ],
    divider: '同时，它还是一个靠谱的备份工具',
    moreCards: [
      {
        title: '任意主机与 NAS',
        body: 'Windows、Linux、Mac——服务器还是工作站都一样——再加上 NAS 共享目录，统一策略保护，不用来回切换三套工具。',
      },
      {
        title: '存储不绑定',
        body: '对象存储还是本地存储，任意 S3 兼容服务商都可以——你的备份不会被绑死在某一家存储厂商身上。',
      },
      {
        title: '单文件秒级恢复',
        body: '只想找回一个文件？浏览任意快照，几秒钟恢复这一个文件就够了，不需要整卷恢复。',
      },
    ],
  },
  howItWorks: {
    kicker: '工作原理',
    title: '备份掘金 治理无扰',
    lead: 'HyperFileLens 把你的文档和代码备份成一份安全、隔离的副本——然后由 SourceLens（我们的开源 Agentic RAG 引擎）直接对这份副本进行推理，全程不碰生产环境。',
    diagramAlt: '你的文档（Word、PDF、Excel、PowerPoint、图片、代码、Markdown）流入 HyperFileLens，进行开源备份、保护和灵活存储。HyperFileLens 生成一份不影响生产环境的隔离安全副本，交给开源 AI agent SourceLens 直接推理——读取、搜索、导航、推理，无需预建索引——产出 AI 洞察：智能客服问答、企业知识库、基于源码的根因分析和可执行的洞察建议。整条链路都是可治理、安全的：访问控制、数据始终在你掌控之中、审计合规、企业级可用。开源，Apache 2.0 协议。',
  },
  openSource: {
    kicker: '社区版',
    titleLines: ['开源社区版 一条命令部署'],
    lead: '在安装了 Docker 的 Ubuntu 主机上安装最新社区版，安装程序会先校验镜像和资产，国内通过 Gitee 和阿里云镜像交付。',
    callout: '社区版免费开源，自带 S3 兼容存储和 AI 模型或 API Key；企业版能力将在后续版本提供。',
    githubCta: '查看 GitHub 仓库',
    starCta: '⭐ 点个 Star',
    engineRepo: 'AI 引擎仓库',
    installGuide: '安装指南',
    installGuideHref: '/zh/docs/getting-started/install',
    betaNote: 'HyperFileLens 目前处于公测阶段。',
    terminal: {
      aria: '社区版在线安装命令',
      title: '社区版',
      subtitle: '最新版本',
      copy: '复制命令',
      copied: '已复制',
      context: '在 Ubuntu 主机上运行',
      comment: '# 安装最新社区版',
      installCommand: [
        'curl -fsSL \\',
        '  https://gitee.com/oneprolabs/hyperfilelens/raw/main/deploy/online/install.sh \\',
        '  | sudo bash -s -- --mirror cn --yes',
      ].join('\n'),
      summaryTitle: '安装程序自动完成',
      summarySteps: '环境检查 · 镜像拉取 · 服务启动',
    },
  },
  contact: {
    kicker: '联系我们',
    title: '联系我们',
    enterprise: {
      title: '企业私有化部署',
      body: '计划为企业做私有化部署？我们可以帮你评估方案。',
      linkLabel: '发邮件给我们',
    },
    support: {
      title: '技术支持',
      body: '遇到问题或者发现 bug？在 GitHub 上提交一下，剩下的交给我们。',
      linkLabel: '提交 Issue',
    },
  },
  footer: {
    blurb: '开源备份工具，内置 Agentic AI 洞察能力，由 OneProLabs 出品。',
    socialAria: 'HyperFileLens GitHub 仓库',
    columns: [
      {
        title: '产品',
        links: [
          { label: '使用场景', href: '#use-cases' },
          { label: '工作原理', href: '#how-it-works' },
          { label: '文档', href: '/zh/docs/getting-started/install', external: true },
          { label: '博客', href: '/zh/blog/' },
          { label: '免费试用', href: '@app', external: true },
        ],
      },
      {
        title: '开源',
        links: [
          { label: 'HyperFileLens', href: '@github', external: true },
          { label: 'AI 引擎', href: '@sourcelens', external: true },
          { label: '发布版本', href: '@releases', external: true },
        ],
      },
      {
        title: '联系我们',
        links: [
          { label: '企业部署', href: 'mailto:oneprolabs@oneprocloud.com' },
          { label: '技术支持', href: '@issues', external: true },
        ],
      },
    ],
    bottom: ['© 2026 OneProLabs', '公测中 · 开放共建'],
  },
}

// Spanish docs are not translated yet, so every documentation link points at the
// English guide on purpose. Revisit once /es/docs exists.
const es: HomeCopy = {
  lang: 'es-ES',
  home: '/es/',
  nav: [
    { label: 'Casos de uso', href: '#use-cases' },
    { label: 'Cómo funciona', href: '#how-it-works' },
    { label: 'Documentación', href: '/docs/getting-started/install', external: true },
    { label: 'Blog', href: '/es/blog/' },
    { label: 'Código abierto', href: '#open-source' },
    { label: 'Contacto', href: '#contact' },
  ],
  header: {
    brandAria: 'Inicio de HyperFileLens',
    navAria: 'Navegación principal',
    githubAria: 'HyperFileLens en GitHub',
    githubLabel: 'GitHub',
    cta: 'Prueba gratis',
    openMenu: 'Abrir menú',
    closeMenu: 'Cerrar menú',
    mobileNavAria: 'Navegación móvil',
  },
  hero: {
    pill: 'OneProLabs · Apache 2.0',
    titleLead: 'Tus copias de seguridad saben',
    titleAccent: 'más de lo que imaginas.',
    lead: 'Haz preguntas directamente a tus copias de seguridad documentales — PDF, Word, Excel, PowerPoint, imágenes, Markdown o cualquier otro formato de texto — sin tocar producción.',
    primaryCta: 'Prueba HyperFileLens',
    freeBadge: 'Gratis',
    secondaryCta: 'Ver en GitHub',
    proofAria: 'Características clave del producto',
    proof: ['Gobernanza de datos', 'Agentic RAG', 'Agent Harness', 'Nunca interrumpe producción'],
    stageAria: 'Vista previa de la consola de HyperFileLens',
    shotAlt: 'Panel de HyperFileLens que muestra el flujo de protección de datos desde el origen en producción hasta el almacenamiento aislado de destino y el simulacro de recuperación, con 452 orígenes y 428,7 TB protegidos, todos los sistemas correctos y la verificación de recuperación lista.',
  },
  useCases: {
    kicker: 'Casos de uso',
    title: 'Respuestas, no solo resultados de búsqueda.',
    cards: [
      {
        title: 'Soporte al cliente',
        body: 'Responde a las preguntas de tus clientes directamente desde tu documentación de producto, PDF y presentaciones: la comprensión multimodal lee todos los formatos, así que no hay que convertir nada antes.',
      },
      {
        title: 'Base de conocimiento corporativa',
        body: 'Convierte especificaciones, políticas y decisiones dispersas — de ingeniería o de toda la empresa — en una base de conocimiento que cualquier equipo puede consultar de verdad, no solo buscar y resumir.',
      },
      {
        title: 'Análisis de causa raíz sobre el código',
        body: 'No es coincidencia de palabras clave ni rastreo de logs: el agente lee y razona sobre tu código fuente real, como lo haría un ingeniero senior, hasta confirmar la causa raíz auténtica.',
      },
    ],
    divider: 'Y sigue siendo una herramienta de backup sólida',
    moreCards: [
      {
        title: 'Cualquier host, más NAS',
        body: 'Windows, Linux y Mac — servidores y estaciones de trabajo por igual — además de recursos compartidos NAS, todo protegido con una sola política y no con tres herramientas distintas.',
      },
      {
        title: 'Sin dependencia de un proveedor',
        body: 'Almacenamiento de objetos o local, cualquier proveedor compatible con S3: tus copias de seguridad no quedan atadas a un único fabricante.',
      },
      {
        title: 'Recuperación de un solo archivo',
        body: '¿Necesitas recuperar un archivo ahora mismo? Explora cualquier snapshot y restaura solo ese archivo en segundos, sin restaurar el volumen completo.',
      },
    ],
  },
  howItWorks: {
    kicker: 'Cómo funciona',
    title: 'Tus archivos no se mueven. El motor va a ellos.',
    lead: 'HyperFileLens copia tus documentos y tu código en una copia segura y aislada; después SourceLens, nuestro motor Agentic RAG de código abierto, razona directamente sobre esa copia sin tocar producción en ningún momento.',
    diagramAlt: 'Tus documentos (Word, PDF, Excel, PowerPoint, imágenes, código, Markdown) llegan a HyperFileLens para su copia de seguridad de código abierto, su protección y un almacenamiento flexible. HyperFileLens genera una copia segura y aislada, sin impacto en producción, sobre la que el agente de IA de código abierto SourceLens razona directamente — leyendo, buscando, navegando y razonando sin índice previo — para producir insights de IA: soporte al cliente, base de conocimiento corporativa, análisis de causa raíz sobre el código fuente e insights accionables. Todo el proceso es gobernable y seguro: control de acceso, los datos siguen bajo tu control, auditoría y cumplimiento, listo para empresa. Código abierto, Apache 2.0.',
  },
  openSource: {
    kicker: 'Comunidad',
    titleLines: ['Código abierto.', 'Instalación con un comando.'],
    lead: 'Instala la última versión Community en un host Ubuntu con Docker. El instalador verifica sus imágenes y recursos antes de modificar el host.',
    callout: 'Community es gratuita y de código abierto, con almacenamiento compatible con S3 y un modelo de IA o una clave de API. Las capacidades Enterprise llegarán en una versión posterior.',
    githubCta: 'Ver en GitHub',
    starCta: '⭐ Dale una estrella',
    engineRepo: 'Repositorio del motor de IA',
    installGuide: 'Guía de instalación',
    installGuideHref: '/docs/getting-started/install',
    betaNote: 'HyperFileLens está actualmente en beta pública.',
    terminal: {
      aria: 'Comando de instalación en línea de Community',
      title: 'Community',
      subtitle: 'Última versión',
      copy: 'Copiar comando',
      copied: 'Copiado',
      context: 'Ejecuta en un host Ubuntu',
      comment: '# Instala la última versión Community',
      installCommand: [
        'curl -fsSL \\',
        '  https://raw.githubusercontent.com/oneprolabs/hyperfilelens/main/deploy/online/install.sh \\',
        '  | sudo bash -s -- --mirror global --yes',
      ].join('\n'),
      summaryTitle: 'El instalador se encarga del resto',
      summarySteps: 'Comprobación del entorno · Descarga de imágenes · Arranque de servicios',
    },
  },
  contact: {
    kicker: 'Contacto',
    title: 'Contacta con nosotros',
    enterprise: {
      title: 'Despliegue empresarial',
      body: '¿Estás planificando un despliegue privado y autoalojado para tu organización? Podemos ayudarte a dimensionarlo.',
      linkLabel: 'Escríbenos',
    },
    support: {
      title: 'Soporte técnico',
      body: '¿Te has encontrado con un problema o un bug? Repórtalo en GitHub y nosotros nos encargamos.',
      linkLabel: 'Abrir un issue',
    },
  },
  footer: {
    blurb: 'Backup de código abierto con insights de IA agéntica, de OneProLabs.',
    socialAria: 'HyperFileLens en GitHub',
    columns: [
      {
        title: 'Producto',
        links: [
          { label: 'Casos de uso', href: '#use-cases' },
          { label: 'Cómo funciona', href: '#how-it-works' },
          { label: 'Documentación', href: '/docs/getting-started/install', external: true },
          { label: 'Blog', href: '/es/blog/' },
          { label: 'Prueba gratis', href: '@app', external: true },
        ],
      },
      {
        title: 'Código abierto',
        links: [
          { label: 'HyperFileLens', href: '@github', external: true },
          { label: 'Motor de IA', href: '@sourcelens', external: true },
          { label: 'Versiones', href: '@releases', external: true },
        ],
      },
      {
        title: 'Contacto',
        links: [
          { label: 'Despliegue empresarial', href: 'mailto:oneprolabs@oneprocloud.com' },
          { label: 'Soporte técnico', href: '@issues', external: true },
        ],
      },
    ],
    bottom: ['© 2026 OneProLabs', 'Beta pública · Desarrollado en abierto'],
  },
}

export const homeCopy = { en, zh, es } as const

export type HomeLocale = keyof typeof homeCopy

export function isHomeLocale(value: string): value is HomeLocale {
  return value in homeCopy
}
