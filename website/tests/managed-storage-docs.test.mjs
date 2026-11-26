import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { transformWithEsbuild } from 'vite'

const website = new URL('../', import.meta.url)
const directory = 'docs/deployment/personal-edition/managed-storage'
const providers = ['huawei', 'aliyun', 'aws', 'rustfs', 'minio']
const slugs = ['index', ...providers]
const docs = {}
// Localized expectations belong beside the localized publication sources.
const zhCopy = JSON.parse(await readFile(
  new URL('zh/.test-fixtures/managed-storage.json', website), 'utf8',
))

for (const locale of ['en', 'zh']) {
  docs[locale] = {}
  for (const slug of slugs) {
    docs[locale][slug] = await readFile(new URL(`${locale}/${directory}/${slug}.md`, website), 'utf8')
  }
}

function policies(source) {
  return [...source.matchAll(/```json\n([\s\S]*?)\n```/g)].map((match) => JSON.parse(match[1]))
}

function array(value) {
  return Array.isArray(value) ? value : [value]
}

test('each provider starts with concrete empty private bucket preparation', () => {
  for (const provider of providers) {
    const en = docs.en[provider]
    const zh = docs.zh[provider]
    assert.match(en, /## Create a Dedicated Empty Bucket/)
    assert.ok(zh.includes(zhCopy.emptyBucketHeading))
    assert.match(en, /prefix `personal\/` is an object-key prefix, not a directory to create/)
    assert.ok(zh.includes(zhCopy.noPrecreatedDirectory))
    assert.match(en, /existing dedicated bucket may be reused only after verifying/)
    assert.ok(en.indexOf('## Create a Dedicated Empty Bucket') < en.indexOf('```json'))
    // Preparation uses an administrator, but runtime policies must not gain
    // any bucket/user administration actions (covered separately below).
    assert.match(en, /administrator/)
  }
  assert.match(docs.en.minio, /mc mb hfl-admin\/hfl-backups-prod/)
  assert.match(docs.zh.minio, /mc mb hfl-admin\/hfl-backups-prod/)
})

test('Alibaba distinguishes key ownership, caller authorization, trust, and proxy errors', () => {
  for (const locale of ['en', 'zh']) {
    const source = docs[locale].aliyun
    assert.match(source, /aliyun sts GetCallerIdentity --profile hfl-sts-controller/)
    for (const field of ['IdentityType', 'RAMUser', 'AccountId', 'Arn', 'NoPermission']) {
      assert.ok(source.includes(field), field)
    }
    assert.match(source, /Admin Console → External Access → Access Configuration → Alibaba Cloud STS Proxy Compatibility/)
    assert.match(source, /NO_PROXY/)
    assert.match(source, /Request ID/)
    assert.doesNotMatch(source, /--AccessKeySecret|--access-key-secret|aliyun sts AssumeRole/)
  }
  assert.match(docs.en.aliyun, /grant the role-call policy at \*\*account scope\*\*/)
  assert.match(docs.en.aliyun, /This setting fixes neither IAM permissions nor `NoPermission`/)
  assert.ok(docs.zh.aliyun.includes(zhCopy.actualKeyOwnerHeading))
  assert.ok(docs.zh.aliyun.includes(zhCopy.accountScopeBinding))
  assert.ok(docs.zh.aliyun.includes(zhCopy.noIamProxyFix))
})

test('AWS documents endpoint-derived STS signing scope and preserves existing settings', () => {
  for (const locale of ['en', 'zh']) {
    const source = docs[locale].aws
    assert.match(source, /\| `https:\/\/sts.amazonaws.com`.*\| `us-east-1` \|/)
    assert.match(source, /\| `https:\/\/sts.ap-southeast-1.amazonaws.com` \| `ap-southeast-1` \|/)
    assert.match(source, /https:\/\/sts.cn-north-1.amazonaws.com.cn/)
    assert.match(source, /aws sts get-caller-identity --profile hfl-sts-controller/)
    assert.match(source, /id_credentials_temp_region-endpoints.html/)
  }
  assert.match(docs.en.aws, /Existing connections and manually edited STS endpoints are preserved/)
})

test('Huawei uses the IAM policy reference and explains the resource and caller accounts', () => {
  for (const locale of ['en', 'zh']) {
    const source = docs[locale].huawei
    assert.match(source, /usermanual-iam\/iam_01_0019.html/)
    assert.doesNotMatch(source, /perms-cfg-obs\/obs_40_0004.html/)
  }
  assert.match(docs.en.huawei, /the final component is the \*\*agency name\*\*, not its ID/)
  assert.ok(docs.zh.huawei.includes(zhCopy.huaweiAccountExample))
  assert.ok(docs.zh.huawei.includes(zhCopy.huaweiAgencyNameNotId))
})

test('self-hosted region guidance matches optional form input and server defaults', () => {
  for (const provider of ['rustfs', 'minio']) {
    assert.match(docs.en[provider], /field may be blank/)
    assert.doesNotMatch(docs.en[provider], /form defaults to `us-east-1`/)
    assert.ok(docs.zh[provider].includes(zhCopy.selfHostedOptionalRegion))
  }
  assert.match(docs.en.index, /### Reproducible Lifecycle Test/)
  assert.match(docs.en.index, /longer than one issued token's lifetime/)
  assert.match(docs.en.index, /Save-time checks do not establish that the bucket is empty or private/)
  assert.ok(docs.zh.index.includes(zhCopy.lifecycleTestHeading))
  assert.ok(docs.zh.index.includes(zhCopy.renewalTest))
  assert.ok(docs.zh.index.includes(zhCopy.saveDoesNotProvePrivate))
})

test('cross-prefix isolation is tested with repository-scoped temporary credentials, not parent keys', () => {
  const en = docs.en.index
  const zh = docs.zh.index
  assert.match(en, /repository A's temporary credentials/)
  assert.match(en, /reading a known test object in A must succeed/)
  assert.match(en, /reading a known test object in repository B must be denied/)
  assert.match(en, /Do not use administrator credentials or connection-wide signing credentials/)
  assert.ok(zh.includes(zhCopy.isolationTemporaryIdentity))
  assert.ok(zh.includes(zhCopy.isolationOwnObjectAllowed))
  assert.ok(zh.includes(zhCopy.isolationOtherObjectDenied))
  assert.ok(zh.includes(zhCopy.isolationNoParentIdentity))
})

test('all six operator guides have matching translations and provider ordering', async () => {
  const enNav = await readFile(new URL('.vitepress/config.mts', website), 'utf8')
  const zhNav = await readFile(new URL('.vitepress/navigation/zh.ts', website), 'utf8')
  for (const [locale, nav, label] of [
    ['en', enNav, 'Personal Edition Operations'],
    ['zh', zhNav, zhCopy.operationsSection],
  ]) {
    const section = nav.slice(nav.indexOf(`text: '${label}'`))
    assert.ok(nav.includes(`text: '${label}'`))
    let previous = -1
    for (const slug of slugs) {
      const suffix = slug === 'index' ? '/' : `/${slug}`
      const route = `/${locale === 'zh' ? 'zh/' : ''}${directory}${suffix}`
      const position = section.indexOf(`link: '${route}'`)
      assert.ok(position > previous, `${locale}: ${route} must be in provider order`)
      previous = position
      const document = docs[locale][slug]
      assert.match(document, /^---\ntitle: .+\ndescription: .+\n---\n/)
      assert.match(document, /::: info /)
      assert.match(document, /Personal Edition/)
      assert.match(document, /Managed Storage/)
      if (locale === 'zh') {
        assert.ok(document.includes(zhCopy.enterprise))
        assert.ok(document.includes(zhCopy.audience))
      } else {
        assert.match(document, /Enterprise/)
        assert.match(document, /ordinary personal|Personal users do not/)
      }
    }
  }
})

test('operator guides retain their page when switching languages in either direction', async () => {
  const source = await readFile(new URL('.vitepress/theme/languages.ts', website), 'utf8')
  const { code } = await transformWithEsbuild(source, 'languages.ts', { loader: 'ts' })
  const { localizedDocPath } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`)
  for (const slug of slugs) {
    const route = `/${directory}${slug === 'index' ? '' : `/${slug}`}`
    const expected = slug === 'index' ? `${route}/` : route
    for (const input of [route, `${route}/`, `/zh${route}`, `/zh${route}/`]) {
      assert.equal(localizedDocPath(input, 'en'), expected)
      assert.equal(localizedDocPath(input, 'zh'), `/zh${expected}`)
    }
  }
})

test('all operator guides explain automatic defaults and gate initial setup on registration safety', () => {
  for (const slug of slugs) {
    const en = docs.en[slug]
    const zh = docs.zh[slug]
    assert.match(en, /[Nn]ew connections are enabled by default/, `${slug}: default enabled state`)
    assert.match(en, /[Ii]f no default connection exists|when no default connection exists/,
      `${slug}: automatic-default condition`)
    assert.match(en, /saving a new connection automatically makes it the default/,
      `${slug}: saving can activate the default immediately`)
    assert.match(en, /[Kk]eep personal registration disabled/, `${slug}: safe initial setup`)
    assert.match(en, /existing test member/, `${slug}: testing does not require public registration`)
    assert.match(en, /before enabling personal registration/, `${slug}: validation before rollout`)
    assert.match(en, /More Actions → Set as Default/, `${slug}: switching an existing default`)
    assert.doesNotMatch(en, /→ enable \/ make default|Make it the default only when/)
    assert.ok(zh.includes(zhCopy.defaultEnabled), `${slug}: Chinese default enabled state`)
    assert.ok(zh.includes(zhCopy.noDefault), `${slug}: Chinese automatic-default condition`)
    assert.ok(zh.includes(zhCopy.automaticDefault), `${slug}: Chinese immediate default`)
    assert.ok(zh.includes(zhCopy.registrationDisabled), `${slug}: Chinese safe initial setup`)
    assert.ok(zh.includes(zhCopy.existingTestMember), `${slug}: Chinese test member`)
    assert.match(zh, new RegExp(zhCopy.registrationRolloutPattern),
      `${slug}: Chinese validation before rollout`)
    assert.match(zh, /More Actions → Set as Default/, `${slug}: Chinese default switching`)
    assert.ok(!zh.includes(zhCopy.obsoleteActivationStep), `${slug}: no manual activation step`)
    assert.ok(!zh.includes(zhCopy.obsoleteDefaultInstruction), `${slug}: no delayed first default`)
    if (slug === 'index') continue
    for (const [locale, document, title] of [
      ['en', en, 'Initial setup and personal registration'],
      ['zh', zh, zhCopy.setupWarningTitle],
    ]) {
      const warning = document.indexOf(`::: warning ${title}`)
      const saveStep = document.indexOf('1. ', warning)
      assert.ok(warning >= 0 && saveStep > warning, `${locale}/${slug}: warn before saving`)
    }
  }
})

test('operator guides document authorized recovery without restoring the Disable action', () => {
  for (const slug of slugs) {
    const en = docs.en[slug]
    const zh = docs.zh[slug]
    for (const document of [en, zh]) {
      assert.ok(document.includes(`**${zhCopy.restoreConnectionAction}**`), slug)
      assert.doesNotMatch(document, /no separate Enable \/ Disable|do not offer an Enable \/ Disable/)
    }
    assert.ok(zh.includes(zhCopy.restoreConnectionValidation), slug)
    assert.ok(zh.includes(zhCopy.restoreConnectionFailure), slug)
    assert.ok(zh.includes(zhCopy.noDisableAction), slug)
    assert.match(en, /with connection-management permission/, slug)
    assert.match(en, /checks temporary-credential issuance and read access again/, slug)
    assert.match(en, /If validation fails, the connection stays disabled and the existing default is unchanged/, slug)
    assert.match(en, /Successful recovery preserves an existing default/, slug)
    assert.match(en, /if none exists, the restored connection automatically becomes the default/, slug)
    assert.match(en, /page does not offer a Disable action/, slug)
  }
})

test('cache guides use the current Production Sites navigation group', async () => {
  const en = await readFile(new URL('en/docs/deployment/repository-cache.md', website), 'utf8')
  const zh = await readFile(new URL('zh/docs/deployment/repository-cache.md', website), 'utf8')
  assert.match(en, /Protection → Production Sites/)
  assert.doesNotMatch(en, /Protection → Source Resources/)
  assert.ok(zh.includes(zhCopy.cacheNavigation))
  assert.ok(!zh.includes(zhCopy.obsoleteCacheNavigation))
})

test('overview separates live repository counts from historical binding safeguards', () => {
  assert.ok(docs.zh.index.includes(zhCopy.liveRepositoryCount))
  assert.ok(docs.zh.index.includes(zhCopy.historicalConnectionProtection))
  assert.match(docs.en.index, /counts current, undeleted repository bindings and excludes deleted historical records/)
  assert.match(docs.en.index, /A count of zero does not necessarily allow connection deletion/)
  assert.match(docs.en.index, /zero count neither removes history nor overrides deletion safeguards/)
})

test('all policy examples parse and match between languages', () => {
  for (const provider of providers) {
    const en = policies(docs.en[provider])
    assert.ok(en.length > 0, provider)
    assert.deepEqual(policies(docs.zh[provider]), en, `${provider}: policy translations must not drift`)
    for (const policy of en) {
      for (const statement of policy.Statement) {
        assert.equal(statement.Effect, 'Allow')
        if (!statement.Action) continue
        for (const action of array(statement.Action)) {
          assert.ok(!action.includes('*'), `${provider}: no wildcard actions`)
          assert.ok(!/CreateBucket|DeleteBucket|PutBucketPolicy|CreateUser/.test(action))
        }
      }
    }
  }
})

const expectedActions = {
  huawei: [
    'obs:bucket:ListBucket', 'obs:bucket:ListBucketVersions',
    'obs:bucket:GetBucketLocation', 'obs:bucket:ListBucketMultipartUploads',
    'obs:object:GetObject', 'obs:object:PutObject', 'obs:object:DeleteObject',
    'obs:object:DeleteObjectVersion', 'obs:object:ListMultipartUploadParts',
    'obs:object:AbortMultipartUpload',
  ],
  aliyun: [
    'oss:ListObjects', 'oss:ListObjectVersions', 'oss:GetBucketInfo', 'oss:ListMultipartUploads',
    'oss:GetObject', 'oss:PutObject', 'oss:DeleteObject', 'oss:DeleteObjectVersion',
    'oss:ListParts', 'oss:AbortMultipartUpload',
  ],
  aws: [
    's3:ListBucket', 's3:ListBucketVersions', 's3:GetBucketLocation', 's3:ListBucketMultipartUploads',
    's3:GetObject', 's3:PutObject', 's3:DeleteObject', 's3:DeleteObjectVersion',
    's3:ListMultipartUploadParts', 's3:AbortMultipartUpload',
  ],
}

test('parent storage policies cover maintenance and cleanup without widening object scope', () => {
  for (const provider of providers) {
    const prefix = provider === 'huawei' ? 'obs:' : provider === 'aliyun' ? 'oss:' : 's3:'
    const policy = policies(docs.en[provider]).find((policy) =>
      policy.Statement.some((statement) => array(statement.Action).some((action) => action?.startsWith(prefix))))
    assert.ok(policy, provider)
    const expected = expectedActions[provider] || expectedActions.aws
    assert.deepEqual(policy.Statement.flatMap((statement) => array(statement.Action)).sort(), expected.toSorted())
    for (const statement of policy.Statement) {
      for (const resource of array(statement.Resource)) {
        assert.ok(resource.includes('hfl-backups-prod'), `${provider}: fixed bucket required`)
        assert.notEqual(resource, '*')
      }
      if (array(statement.Action).some((action) => /:(?:object:)?(?:GetObject|PutObject|DeleteObject|ListParts|ListMultipartUploadParts|AbortMultipartUpload)/.test(action))) {
        assert.ok(array(statement.Resource).every((resource) =>
          resource.endsWith('hfl-backups-prod/personal/repositories/*')), `${provider}: object prefix required`)
      }
    }
    const listing = policy.Statement.find((statement) =>
      array(statement.Action).some((action) => /:(?:bucket:)?(?:ListBucket|ListObjects)$/.test(action)))
    assert.ok(listing.Condition, `${provider}: normal listing cannot be bucket-wide`)
    if (provider === 'huawei') {
      assert.deepEqual(listing.Condition, {
        StringStartWith: { 'obs:prefix': ['personal/repositories/'] },
      })
    } else {
      assert.match(JSON.stringify(listing.Condition), /personal\/repositories\/\*/)
    }
    const multipart = policy.Statement.find((statement) =>
      array(statement.Action).some((action) => /List(?:Bucket)?MultipartUploads$/.test(action)))
    assert.ok(multipart, `${provider}: physical cleanup needs multipart listing`)
    assert.equal(multipart.Condition, undefined)
    assert.match(docs.en[provider], /bucket-scoped metadata/)
  }
})

test('cloud caller and trust policies target only the dedicated user and role', () => {
  for (const [provider, arn] of [
    ['aws', 'arn:aws:iam::<ACCOUNT_ID>'],
    ['aliyun', 'acs:ram::<ACCOUNT_ID>'],
  ]) {
    const examples = policies(docs.en[provider])
    const caller = examples.find((policy) => policy.Statement[0].Action === 'sts:AssumeRole'
      && policy.Statement[0].Resource)
    assert.equal(caller.Statement[0].Resource, `${arn}:role/hfl-managed-storage`)
    const trust = examples.find((policy) => policy.Statement[0].Principal)
    assert.match(JSON.stringify(trust.Statement[0].Principal), /:user\/hfl-sts-controller/)
    assert.doesNotMatch(JSON.stringify(trust), /:root/)
  }
  const huawei = policies(docs.en.huawei).find((policy) =>
    policy.Statement[0].Action.includes('iam:tokens:assume'))
  assert.deepEqual(huawei.Statement[0].Resource, { uri: ['/iam/agencies/hfl-managed-storage'] })
})

test('provider-specific fields and compatibility boundaries are not collapsed into generic S3', () => {
  assert.match(docs.en.huawei, /Agency Name/)
  assert.match(docs.en.huawei, /Domain ID/)
  assert.match(docs.en.huawei, /not every European/)
  assert.match(docs.en.aliyun, /16 digits/)
  assert.match(docs.en.aliyun, /validator does not accept/)
  for (const provider of ['rustfs', 'minio']) {
    assert.match(docs.en[provider], /not a service account|not administrator\/root or Service Account/i)
    assert.match(docs.en[provider], /Path Style/)
    assert.match(docs.en[provider], /same scheme\/host\/port/)
    assert.match(docs.en[provider], /Not shown for this provider/)
  }
  assert.match(docs.en.rustfs, /SigV4 `s3`/)
  assert.match(docs.en.minio, /mc admin user add/)
  assert.match(docs.en.minio, /mc admin policy attach/)
})

test('operator guides use formal published instructions rather than internal request templates', () => {
  for (const slug of slugs) {
    const zh = docs.zh[slug]
    const en = docs.en[slug]
    for (const term of zhCopy.internalCommunicationTerms) {
      assert.ok(!zh.includes(term), `zh/${slug}: internal communication term ${term}`)
    }
    assert.doesNotMatch(en, /colleague|teammate|handoff|Please prepare|\nReturn:|Delegate Preparation/i,
      `en/${slug}: internal request template`)
    if (slug === 'index') {
      assert.ok(zh.includes(`## ${zhCopy.credentialManagementHeading}`))
      assert.match(en, /## Configuration Preparation and Credential Management/)
      continue
    }
    for (const [locale, document, heading, detailHeading] of [
      ['zh', zh, zhCopy.checklistHeading, zhCopy.configurationDetailsHeading],
      ['en', en, 'Configuration Checklist', 'Required Configuration Details'],
    ]) {
      const checklist = document.split(`## ${heading}\n`)[1]?.split('\n## ')[0]
      assert.ok(checklist, `${locale}/${slug}: configuration checklist`)
      assert.ok(checklist.includes(`### ${detailHeading}`),
        `${locale}/${slug}: required configuration details`)
      assert.match(checklist, /\| Bucket \|/)
      assert.match(checklist, /\| Access Key \/ Secret Key \|/)
      assert.match(checklist, /\| Object Prefix \|/)
      if (locale === 'zh') assert.ok(checklist.includes(zhCopy.secureChannel))
      else assert.match(checklist, /secure channel/)
      assert.doesNotMatch(checklist, /```text/)
      if (slug === 'huawei') {
        assert.match(checklist, /\| Agency Name \|/)
        assert.match(checklist, /\| Domain ID \|/)
      } else if (['aliyun', 'aws'].includes(slug)) {
        assert.match(checklist, /\| Role ARN \|/)
        assert.match(checklist, /\| Identity Service Endpoint \|/)
      } else {
        assert.match(checklist, /TLS/)
        assert.match(checklist, /us-east-1/)
      }
    }
  }
})

test('each provider has field mapping, credential security, and read-only preflight caveat', () => {
  for (const provider of providers) {
    for (const [locale, source] of Object.entries(docs)) {
      const document = source[provider]
      assert.match(document, /Endpoint URL/)
      assert.match(document, /Object Prefix/)
      assert.match(document, /https:\/\/(?:support\.huaweicloud\.com|help\.aliyun\.com|docs\.aws\.amazon\.com|docs\.rustfs\.com|github\.com\/minio|docs\.min\.io)/)
      if (locale === 'zh') {
        assert.ok(document.includes(zhCopy.secureChannel))
        assert.ok(document.includes(zhCopy.readOnlyCaveat))
      } else {
        assert.match(document, /secure channel|secure delivery/)
        assert.match(document, /not write access/)
      }
      assert.ok(!document.includes('/root/github_hfl'), `${locale}: do not publish workspace paths`)
    }
  }
})
