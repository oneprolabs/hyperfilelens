<script setup lang="ts">
import ModulePage from '../../components/ModulePage.vue'

const operators = [
  { token: '#', meaning: 'Starts a comment line. Comments are ignored.' },
  { token: '*', meaning: 'Matches zero or more characters in a file or folder name.' },
  { token: '**', meaning: 'Matches zero or more directories.' },
  { token: '?', meaning: 'Matches exactly one character.' },
  { token: '[0-9]', meaning: 'Matches one digit.' },
  { token: '[a-z]', meaning: 'Matches one lowercase letter.' },
  { token: '[A-Z]', meaning: 'Matches one uppercase letter.' },
  { token: '[abc]', meaning: 'Matches one of the listed characters.' },
  { token: '/', meaning: 'At the start of a rule, limits the match to the root of the selected source.' },
]

const examples = [
  { rule: '*.log', use: 'Exclude all files ending in .log.' },
  { rule: '*.db*', use: 'Exclude files whose extension starts with .db.' },
  { rule: 'tmp.db', use: 'Exclude files named tmp.db anywhere under the selected source.' },
  { rule: '/logs/*', use: 'Exclude files inside the root-level logs folder.' },
  { rule: '**/logs/**', use: 'Exclude every logs folder at any depth.' },
  { rule: 'chapters/**/*.log', use: 'Exclude .log files under chapters at any depth.' },
  { rule: '?tmp.db', use: 'Exclude names with exactly one character before tmp.db, such as atmp.db.' },
  { rule: '[a-z]*tmp.db', use: 'Exclude names starting with a lowercase letter and ending with tmp.db.' },
]

const recipes = [
  {
    title: 'Skip temporary files and logs',
    lines: ['*.tmp', '*.log', '*.bak', '*.old', '*.swp'],
  },
  {
    title: 'Skip development caches',
    lines: ['**/node_modules/**', '**/.git/**', '**/.cache/**', '**/__pycache__/**'],
  },
  {
    title: 'Skip a folder everywhere',
    lines: ['**/cache/**'],
  },
]
</script>

<template>
  <ModulePage title="File Filter Rule Guide">
    <div class="rule-guide">
      <section class="rule-guide__section">
        <h2>Rule Basics</h2>
        <p>
          Write one rule per line. Empty lines are ignored. Lines starting with
          <code>#</code> are comments. Rules are case-sensitive and are applied
          in order.
        </p>
      </section>

      <section class="rule-guide__section">
        <h2>Pattern Operators</h2>
        <div class="rule-guide__table">
          <div
            v-for="row in operators"
            :key="row.token"
            class="rule-guide__table-row"
          >
            <code>{{ row.token }}</code>
            <span>{{ row.meaning }}</span>
          </div>
        </div>
      </section>

      <section class="rule-guide__section">
        <h2>Common Examples</h2>
        <div class="rule-guide__table">
          <div
            v-for="row in examples"
            :key="row.rule"
            class="rule-guide__table-row"
          >
            <code>{{ row.rule }}</code>
            <span>{{ row.use }}</span>
          </div>
        </div>
      </section>

      <section class="rule-guide__section">
        <h2>Reusable Recipes</h2>
        <div class="rule-guide__recipes">
          <article
            v-for="recipe in recipes"
            :key="recipe.title"
            class="rule-guide__recipe"
          >
            <h3>{{ recipe.title }}</h3>
            <pre><code>{{ recipe.lines.join('\n') }}</code></pre>
          </article>
        </div>
      </section>

      <section class="rule-guide__section">
        <h2>Advanced Settings</h2>
        <div class="rule-guide__table">
          <div class="rule-guide__table-row">
            <strong>Large file limit</strong>
            <span>Excludes files larger than the configured size.</span>
          </div>
          <div class="rule-guide__table-row">
            <strong>Cache directories</strong>
            <span>Excludes known cache folders to reduce scan time and storage usage.</span>
          </div>
          <div class="rule-guide__table-row">
            <strong>Current filesystem only</strong>
            <span>Stays within the selected source filesystem and does not cross into other mounted filesystems.</span>
          </div>
        </div>
      </section>

      <section class="rule-guide__section rule-guide__section--note">
        <h2>Before Saving</h2>
        <p>
          Review the final rules preview and verify that expected files are
          still included. A broad rule such as <code>*.*</code> can skip much
          more than intended.
        </p>
      </section>
    </div>
  </ModulePage>
</template>

<style scoped>
.rule-guide {
  display: flex;
  flex-direction: column;
  gap: 18px;
  max-width: 980px;
}

.rule-guide__section {
  padding: 20px;
  border: 1px solid rgba(226, 232, 240, 0.95);
  border-radius: 8px;
  background: #fff;
}

.rule-guide__section h2 {
  margin: 0 0 10px;
  color: rgb(15 23 42);
  font-size: 18px;
  font-weight: 700;
  line-height: 1.35;
}

.rule-guide__section h3 {
  margin: 0 0 10px;
  color: rgb(30 41 59);
  font-size: 14px;
  font-weight: 650;
}

.rule-guide__section p {
  margin: 0;
  color: rgb(71 85 105);
  font-size: 14px;
  line-height: 1.65;
}

.rule-guide__section p + p {
  margin-top: 8px;
}

.rule-guide code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
}

.rule-guide__section p code,
.rule-guide__table-row code {
  padding: 2px 6px;
  border-radius: 5px;
  background: rgb(241 245 249);
  color: rgb(15 23 42);
  font-size: 12px;
}

.rule-guide__table {
  display: flex;
  flex-direction: column;
  border: 1px solid rgba(226, 232, 240, 0.95);
  border-radius: 8px;
  overflow: hidden;
}

.rule-guide__table-row {
  display: grid;
  grid-template-columns: minmax(130px, 180px) minmax(0, 1fr);
  gap: 16px;
  align-items: start;
  padding: 12px 14px;
  border-bottom: 1px solid rgba(226, 232, 240, 0.95);
  color: rgb(71 85 105);
  font-size: 13px;
  line-height: 1.55;
}

.rule-guide__table-row:last-child {
  border-bottom: 0;
}

.rule-guide__table-row strong {
  color: rgb(30 41 59);
  font-size: 13px;
  font-weight: 650;
}

.rule-guide__recipes {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.rule-guide__recipe {
  min-width: 0;
  padding: 14px;
  border: 1px solid rgba(226, 232, 240, 0.95);
  border-radius: 8px;
  background: rgb(248 250 252);
}

.rule-guide__recipe pre {
  margin: 0;
  padding: 12px;
  overflow-x: auto;
  border-radius: 8px;
  background: rgb(15 23 42);
  color: rgb(226 232 240);
  font-size: 12px;
  line-height: 1.55;
}

.rule-guide__section--note {
  border-color: rgba(245, 158, 11, 0.35);
  background: rgba(255, 251, 235, 0.75);
}

@media (max-width: 720px) {
  .rule-guide__table-row,
  .rule-guide__recipes {
    grid-template-columns: 1fr;
  }
}
</style>
