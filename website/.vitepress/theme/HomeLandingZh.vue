<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  trackWebsiteOpenApp,
  type WebsiteOpenAppPlacement,
} from './analytics'
import LanguageSwitcher from './LanguageSwitcher.vue'

const appOrigin = ref('')

function validOrigin(value: string): string {
  try {
    const parsed = new URL(value)
    if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password) return ''
    if (parsed.pathname !== '/' || parsed.search || parsed.hash) return ''
    return parsed.origin
  } catch {
    return ''
  }
}

function directAppOrigin(): string {
  const hostname = window.location.hostname || '127.0.0.1'
  const host = hostname.includes(':') ? `[${hostname}]` : hostname
  return `https://${host}:11443`
}

onMounted(() => {
  appOrigin.value = validOrigin(window.__HFL_WEBSITE_CONFIG__?.appUrl || '') || directAppOrigin()
})

const loginUrl = computed(() => `${appOrigin.value || '#'}${appOrigin.value ? '/login' : ''}`)

const githubUrl = 'https://github.com/HyperBDR/hyperfilelens'
const sourceLensUrl = 'https://github.com/HyperBDR/sourcelens'

function openApp(event: MouseEvent, placement: WebsiteOpenAppPlacement) {
  const target = loginUrl.value
  if (!target || target === '#') return
  if (
    event.button !== 0
    || event.metaKey
    || event.ctrlKey
    || event.shiftKey
    || event.altKey
  ) {
    trackWebsiteOpenApp(placement)
    return
  }
  event.preventDefault()
  trackWebsiteOpenApp(placement, () => window.location.assign(target))
}
</script>

<template>
  <div class="hfl-site">
    <svg class="icon-sprite" aria-hidden="true">
      <symbol id="icon-arrow" viewBox="0 0 24 24">
        <path d="M5 12h14M13 6l6 6-6 6" />
      </symbol>
      <symbol id="icon-github" viewBox="0 0 24 24">
        <path d="M12 2a10 10 0 0 0-3.16 19.49c.5.09.68-.22.68-.48v-1.86c-2.78.6-3.37-1.18-3.37-1.18-.45-1.16-1.11-1.47-1.11-1.47-.91-.62.07-.61.07-.61 1 .07 1.53 1.03 1.53 1.03.9 1.53 2.35 1.09 2.92.83.09-.65.35-1.09.64-1.34-2.22-.25-4.55-1.11-4.55-4.94 0-1.09.39-1.98 1.03-2.68-.1-.25-.45-1.27.1-2.64 0 0 .84-.27 2.75 1.02A9.6 9.6 0 0 1 12 6.84a9.6 9.6 0 0 1 2.5.34c1.92-1.3 2.76-1.02 2.76-1.02.55 1.37.2 2.39.1 2.64.64.7 1.03 1.59 1.03 2.68 0 3.84-2.34 4.68-4.57 4.93.36.31.68.92.68 1.86v2.75c0 .27.18.58.69.48A10 10 0 0 0 12 2Z" fill="currentColor" stroke="none" />
      </symbol>
      <symbol id="icon-shield" viewBox="0 0 24 24">
        <path d="M12 3 5 6v5c0 4.6 2.8 8 7 10 4.2-2 7-5.4 7-10V6l-7-3Z" /><path d="m9 12 2 2 4-4" />
      </symbol>
      <symbol id="icon-network" viewBox="0 0 24 24">
        <rect x="9" y="3" width="6" height="6" rx="2" /><rect x="3" y="15" width="6" height="6" rx="2" /><rect x="15" y="15" width="6" height="6" rx="2" /><path d="M12 9v3M6 15v-3h12v3" />
      </symbol>
      <symbol id="icon-search" viewBox="0 0 24 24">
        <circle cx="11" cy="11" r="7" /><path d="m16 16 5 5" />
      </symbol>
      <symbol id="icon-restore" viewBox="0 0 24 24">
        <path d="M4 8v5h5" /><path d="M5.8 17.2A8 8 0 1 0 4.2 9" /><path d="M12 7v5l3 2" />
      </symbol>
      <symbol id="icon-server" viewBox="0 0 24 24">
        <rect x="3" y="4" width="18" height="6" rx="2" /><rect x="3" y="14" width="18" height="6" rx="2" /><path d="M7 7h.01M7 17h.01M11 7h6M11 17h6" />
      </symbol>
      <symbol id="icon-code" viewBox="0 0 24 24">
        <path d="m8 9-4 3 4 3M16 9l4 3-4 3M14 5l-4 14" />
      </symbol>
      <symbol id="icon-check" viewBox="0 0 24 24">
        <path d="m5 12 4 4L19 6" />
      </symbol>
      <symbol id="icon-building" viewBox="0 0 24 24">
        <path d="M4 21V5l8-3 8 3v16M8 8h2M14 8h2M8 12h2M14 12h2M8 16h2M14 16h2M2 21h20" />
      </symbol>
      <symbol id="icon-globe" viewBox="0 0 24 24">
        <circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c2.5 2.5 4 5.5 4 9s-1.5 6.5-4 9c-2.5-2.5-4-5.5-4-9s1.5-6.5 4-9Z" />
      </symbol>
      <symbol id="icon-chevron" viewBox="0 0 24 24">
        <path d="m6 9 6 6 6-6" />
      </symbol>
    </svg>

    <header class="site-header-wrap">
      <div class="site-header">
        <a class="brand" href="/zh/" aria-label="HyperFileLens 首页">
          <img src="/logo-mark.svg" alt="" width="34" height="34" />
          <span>HyperFileLens</span>
        </a>
        <nav aria-label="主导航">
          <a href="#use-cases">使用场景</a>
          <a href="#how-it-works">工作原理</a>
          <a href="#open-source">开源</a>
          <a href="#contact">联系我们</a>
        </nav>
        <div class="header-actions">
          <LanguageSwitcher current="zh" />
          <a class="github-link" :href="githubUrl" aria-label="HyperFileLens GitHub 仓库">
            <svg aria-hidden="true"><use href="#icon-github" /></svg>
            <span>GitHub</span>
          </a>
          <a class="header-cta" :href="loginUrl" @click="openApp($event, 'header')">免费试用</a>
        </div>
      </div>
    </header>

    <main>
      <section class="hero" aria-labelledby="hero-title">
        <div class="hero-backdrop" aria-hidden="true"></div>
        <div class="hero-copy">
          <a class="open-source-pill" :href="githubUrl">
            <svg aria-hidden="true"><use href="#icon-github" /></svg>
            OneProLabs · Apache 2.0
            <svg aria-hidden="true"><use href="#icon-arrow" /></svg>
          </a>
          <h1 id="hero-title">你的备份<br /><span>藏着意想不到的答案</span></h1>
          <p class="hero-lead">
            直接对你的文档备份进行问答——PDF、图片、PPT、Word、Excel，格式不限，
            且不影响生产环境。
          </p>
          <div class="hero-actions">
            <a class="button button-primary" :href="loginUrl" @click="openApp($event, 'hero')">
              <img src="/logo-mark-white.svg" alt="" class="button-logo" />
              试用 HyperFileLens
              <svg aria-hidden="true"><use href="#icon-arrow" /></svg>
              <span class="free-badge">免费</span>
            </a>
            <a class="button button-secondary" :href="githubUrl">
              <svg aria-hidden="true"><use href="#icon-github" /></svg>
              查看 GitHub 仓库
            </a>
          </div>
          <div class="hero-proof" aria-label="核心特性">
            <span><svg aria-hidden="true"><use href="#icon-check" /></svg>数据治理</span>
            <span><svg aria-hidden="true"><use href="#icon-check" /></svg>Agentic RAG</span>
            <span><svg aria-hidden="true"><use href="#icon-check" /></svg>Agent Harness</span>
            <span><svg aria-hidden="true"><use href="#icon-check" /></svg>不影响生产环境</span>
          </div>
        </div>

        <div class="product-stage" aria-label="HyperFileLens 控制台预览">
          <img
            class="product-shot"
            src="/product-overview.webp"
            width="1672"
            height="941"
            alt="HyperFileLens 概览仪表盘：数据保护流水线从生产源到隔离的目标存储，再到恢复演练，已保护 452 个源、428.7 TB 数据，系统状态健康，恢复验证就绪。"
          />
        </div>
      </section>

      <section id="use-cases" class="section-block use-cases" aria-labelledby="use-cases-title">
        <div class="section-heading centered">
          <p class="section-kicker">使用场景</p>
          <h2 id="use-cases-title">不止是检索，更是答案。</h2>
        </div>
        <div class="use-case-grid">
          <article>
            <div class="use-case-head">
              <div class="card-icon violet"><svg aria-hidden="true"><use href="#icon-search" /></svg></div>
              <h3>智能客服问答</h3>
            </div>
            <p>直接基于产品文档、PDF、PPT 回答客户问题——多模态识别跨格式读取，不用预先转换格式。</p>
          </article>
          <article>
            <div class="use-case-head">
              <div class="card-icon"><svg aria-hidden="true"><use href="#icon-network" /></svg></div>
              <h3>企业知识库</h3>
            </div>
            <p>把分散的技术文档、管理制度、决策记录——不管是研发的还是全公司的——整合成一个团队真正能查询的知识库，而不只是搜索和摘要。</p>
          </article>
          <article>
            <div class="use-case-head">
              <div class="card-icon amber"><svg aria-hidden="true"><use href="#icon-code" /></svg></div>
              <h3>基于源码的根因分析</h3>
            </div>
            <p>不是关键词匹配，也不是扫日志——agent 会像资深工程师一样真正阅读、推理你的源代码，直到确认真正的根因。</p>
          </article>
          <p class="use-case-divider">同时，它还是一个靠谱的备份工具</p>
          <article>
            <div class="use-case-head">
              <div class="card-icon"><svg aria-hidden="true"><use href="#icon-server" /></svg></div>
              <h3>任意主机，还有 NAS</h3>
            </div>
            <p>Windows、Linux、Mac——服务器还是工作站都一样——再加上 NAS 共享目录，统一策略保护，不用来回切换三套工具。</p>
          </article>
          <article>
            <div class="use-case-head">
              <div class="card-icon violet"><svg aria-hidden="true"><use href="#icon-network" /></svg></div>
              <h3>存储不绑定</h3>
            </div>
            <p>对象存储还是本地存储，任意 S3 兼容服务商都可以——你的备份不会被绑死在某一家存储厂商身上。</p>
          </article>
          <article>
            <div class="use-case-head">
              <div class="card-icon amber"><svg aria-hidden="true"><use href="#icon-restore" /></svg></div>
              <h3>单文件秒级恢复</h3>
            </div>
            <p>只想找回一个文件？浏览任意快照，几秒钟恢复这一个文件就够了，不需要整卷恢复。</p>
          </article>
        </div>
      </section>

      <section id="how-it-works" class="section-block how-it-works" aria-labelledby="flow-title">
        <div class="section-heading centered">
          <p class="section-kicker">工作原理</p>
          <h2 id="flow-title">文件不用动，引擎主动找上门。</h2>
          <p>HyperFileLens 把你的文档和代码备份成一份安全、隔离的副本——然后由开源 AI agent 直接对这份副本进行推理，全程不碰生产环境。</p>
        </div>
        <div class="engine-diagram-frame">
          <img
            class="engine-diagram"
            src="/how-it-works.webp"
            width="1600"
            height="878"
            loading="lazy"
            alt="你的文档（Word、PDF、Excel、PowerPoint、图片、代码、Markdown）流入 HyperFileLens，进行开源备份、保护和灵活存储。HyperFileLens 生成一份不影响生产环境的隔离安全副本，交给开源 AI agent SourceLens 直接推理——读取、搜索、导航、推理，无需预建索引——产出 AI 洞察：智能客服问答、企业知识库、基于源码的根因分析和可执行的洞察建议。整条链路都是可治理、安全的：访问控制、数据始终在你掌控之中、审计合规、企业级可用。开源，Apache 2.0 协议。"
          />
        </div>
      </section>

      <section id="open-source" class="open-source-section" aria-labelledby="open-source-title">
        <div class="open-source-grid">
          <div class="open-source-copy">
            <p class="section-kicker dark-kicker">开源</p>
            <h2 id="open-source-title">看得见，跑得起，改得动。</h2>
            <p>HyperFileLens 和背后的 AI 引擎全部开源。你可以查看架构设计、跟进开发进度，或者直接在 GitHub 上参与贡献。</p>
            <div class="open-source-callout">
              <svg aria-hidden="true"><use href="#icon-check" /></svg>
              <p>不绑定任何厂商——自带 S3 兼容存储，自带 AI 模型或 API Key，自托管或托管部署都行，随时可以换。</p>
            </div>
            <div class="open-source-actions">
              <a class="button button-light" :href="githubUrl"><svg aria-hidden="true"><use href="#icon-github" /></svg>查看 GitHub 仓库</a>
              <a class="button button-dark-outline" :href="githubUrl">⭐ 点个 Star</a>
            </div>
            <div class="open-source-links">
              <a class="source-link" :href="sourceLensUrl"><svg aria-hidden="true"><use href="#icon-github" /></svg>AI 引擎仓库</a>
              <a class="source-link" :href="`${githubUrl}/releases`">查看发布版本 <svg aria-hidden="true"><use href="#icon-arrow" /></svg></a>
            </div>
            <p class="beta-note">HyperFileLens 目前处于公测阶段。</p>
          </div>
          <div class="terminal-card" aria-label="HyperFileLens 部署命令示例">
            <div class="terminal-bar"><span><i></i><i></i><i></i></span><b>部署 · bash</b></div>
            <pre><code><span class="terminal-comment"># 完整离线安装包</span>
<span class="terminal-prompt">$</span> tar -xzf hyperfilelens-release.tar.gz
<span class="terminal-prompt">$</span> cd hyperfilelens
<span class="terminal-prompt">$</span> sudo ./install.sh install

<span class="terminal-success">✓</span> 环境校验通过
<span class="terminal-success">✓</span> 镜像已加载到本地
<span class="terminal-success">✓</span> HyperFileLens 已就绪</code></pre>
          </div>
        </div>
      </section>

      <section id="contact" class="section-block contact-section" aria-labelledby="contact-title">
        <div class="section-heading centered">
          <p class="section-kicker">联系我们</p>
          <h2 id="contact-title">联系我们</h2>
        </div>
        <div class="contact-grid">
          <article>
            <div class="use-case-head">
              <div class="card-icon"><svg aria-hidden="true"><use href="#icon-building" /></svg></div>
              <h3>企业私有化部署</h3>
            </div>
            <p>计划为企业做私有化部署？我们可以帮你评估方案。</p>
            <a class="text-link" href="mailto:oneprolabs@oneprocloud.com">发邮件给我们 <svg aria-hidden="true"><use href="#icon-arrow" /></svg></a>
          </article>
          <article>
            <div class="use-case-head">
              <div class="card-icon violet"><svg aria-hidden="true"><use href="#icon-github" /></svg></div>
              <h3>技术支持</h3>
            </div>
            <p>遇到问题或者发现 bug？在 GitHub 上提交一下，剩下的交给我们。</p>
            <a class="text-link" :href="`${githubUrl}/issues`">提交 Issue <svg aria-hidden="true"><use href="#icon-arrow" /></svg></a>
          </article>
        </div>
      </section>

    </main>

    <footer>
      <div class="footer-brand">
        <a class="brand" href="/zh/"><img src="/logo-mark.svg" alt="" width="32" height="32" /><span>HyperFileLens</span></a>
        <p>开源备份工具，内置 Agentic AI 洞察能力，由 OneProLabs 出品。</p>
        <a class="footer-social" :href="githubUrl" aria-label="HyperFileLens GitHub 仓库"><svg aria-hidden="true"><use href="#icon-github" /></svg></a>
      </div>
      <div class="footer-links">
        <div><strong>产品</strong><a href="#use-cases">使用场景</a><a href="#how-it-works">工作原理</a><a :href="loginUrl" @click="openApp($event, 'footer')">免费试用</a></div>
        <div><strong>开源</strong><a :href="githubUrl">HyperFileLens</a><a :href="sourceLensUrl">AI 引擎</a><a :href="`${githubUrl}/releases`">发布版本</a></div>
        <div><strong>联系我们</strong><a href="mailto:oneprolabs@oneprocloud.com">企业部署</a><a :href="`${githubUrl}/issues`">技术支持</a></div>
      </div>
      <div class="footer-bottom"><span>© 2026 OneProLabs</span><span>公测中 · 开放共建</span></div>
    </footer>
  </div>
</template>
