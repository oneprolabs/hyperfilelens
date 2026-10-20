// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'
import {
  applyProviderImport,
  createProviderValidationRun,
  confirmProviderReset,
  exportProviders,
  normalizedStorageProviderSnapshot,
  reviewProviderReset,
  type ProviderImportReview,
  type ProviderResetReview,
} from './storageProviderCatalogApi'

vi.mock('./api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./api')>()
  return { ...actual, api: vi.fn() }
})

afterEach(() => vi.clearAllMocks())

describe('Storage Provider Catalog API', () => {
  it('submits credentials only to the write-only validation endpoint', async () => {
    vi.mocked(api).mockResolvedValue({ id: 'run-1' })

    await createProviderValidationRun({
      provider_id: 'aliyun',
      region_ids: ['oss-cn-hangzhou'],
      access_key_id: 'access-value',
      secret_access_key: 'secret-value',
      candidate_config: {
        id: 'aliyun',
        display_name: 'Alibaba Cloud OSS',
        enabled: true,
        regions: [],
      },
    })

    expect(api).toHaveBeenCalledWith('/api/v1/platform-ops/storage-provider-validation-runs', {
      method: 'POST',
      body: JSON.stringify({
        provider_id: 'aliyun',
        region_ids: ['oss-cn-hangzhou'],
        access_key_id: 'access-value',
        secret_access_key: 'secret-value',
        candidate_config: {
          id: 'aliyun',
          display_name: 'Alibaba Cloud OSS',
          enabled: true,
          regions: [],
        },
      }),
    })
  })

  it('re-submits the reviewed document, checksum, token, and exact risks for Apply', async () => {
    vi.mocked(api).mockResolvedValue({ applied: true })
    const review = {
      input_checksum: 'a'.repeat(64),
      review_token: 'opaque-review-token',
    } as ProviderImportReview

    await applyProviderImport('{"schema_version":1,"providers":[]}', review, ['validation:not_run:aliyun'])

    expect(api).toHaveBeenCalledWith('/api/v1/platform-ops/storage-providers/import/apply', {
      method: 'POST',
      body: JSON.stringify({
        content: '{"schema_version":1,"providers":[]}',
        input_checksum: 'a'.repeat(64),
        review_token: 'opaque-review-token',
        risk_confirmations: ['validation:not_run:aliyun'],
      }),
    })
  })

  it('uses distinct review endpoints for drawer and selected-provider restoration', async () => {
    vi.mocked(api).mockResolvedValue({ scope: 'selected' })

    await reviewProviderReset('aliyun')
    await reviewProviderReset(['aliyun'])
    await reviewProviderReset(['aliyun', 'aws'])

    expect(vi.mocked(api).mock.calls.map(([path]) => path)).toEqual([
      '/api/v1/platform-ops/storage-providers/aliyun/reset/review',
      '/api/v1/platform-ops/storage-providers/reset-selected/review',
      '/api/v1/platform-ops/storage-providers/reset-selected/review',
    ])
    expect(JSON.parse(vi.mocked(api).mock.calls[2]![1]!.body as string))
      .toEqual({ provider_ids: ['aliyun', 'aws'] })
  })

  it('never falls back to all providers for empty, missing, or duplicate selections', () => {
    expect(() => exportProviders([])).toThrow()
    expect(() => reviewProviderReset([])).toThrow()
    expect(() => reviewProviderReset(['aliyun', 'aliyun'])).toThrow()
    expect(() => reviewProviderReset(undefined as unknown as string)).toThrow()
    expect(api).not.toHaveBeenCalled()
  })

  it('exports only explicit selected IDs', async () => {
    vi.mocked(api).mockResolvedValue({ providers: [] })
    await exportProviders(['aliyun', 'aws'])
    expect(api).toHaveBeenCalledWith(
      '/api/v1/platform-ops/storage-providers/export?provider_ids=aliyun%2Caws',
    )
  })

  it('confirms the reviewed selection and token without using a global endpoint', async () => {
    vi.mocked(api).mockResolvedValue({ reset: true })
    const review: ProviderResetReview = {
      scope: 'selected', provider_ids: ['aliyun', 'aws'], providers: [],
      reset_token: 'selected-token', expires_at: '2099-01-01T00:00:00Z',
    }
    await confirmProviderReset(review)
    expect(api).toHaveBeenCalledWith('/api/v1/platform-ops/storage-providers/reset-selected', {
      method: 'POST',
      body: JSON.stringify({ reset_token: 'selected-token', provider_ids: ['aliyun', 'aws'] }),
    })
  })

  it('rejects global reviews and incompatible single-provider scopes before sending requests', () => {
    const review: ProviderResetReview = {
      scope: 'all', provider_ids: ['aliyun', 'aws'], providers: [],
      reset_token: 'scope-token', expires_at: '2099-01-01T00:00:00Z',
    }
    expect(() => confirmProviderReset(review)).toThrow()
    expect(() => confirmProviderReset({ ...review, scope: 'selected' }, 'aliyun')).toThrow()
    expect(() => confirmProviderReset({ ...review, scope: 'provider', provider_ids: ['aws'] }, 'aliyun')).toThrow()
    expect(api).not.toHaveBeenCalled()
  })

  it('compares validation candidates after server-equivalent string normalization', () => {
    const candidate = {
      regions: [{
        use_tls: true as const,
        s3_url_style: 'virtual_hosted' as const,
        driver: 's3' as const,
        internal_endpoint: ' OBS-CN.EXAMPLE.COM ',
        external_endpoint: ' OBS-CN.EXAMPLE.COM ',
        region_group_en: ' Asia Pacific ',
        region_group: ' asia_pacific ',
        display_name: ' Guangzhou ',
        id: ' cn-south-1 ',
      }],
      enabled: true,
      display_name: ' Huawei Cloud ',
      id: 'huaweicloud',
    }
    const normalized = {
      id: 'huaweicloud',
      display_name: 'Huawei Cloud',
      enabled: true,
      regions: [{
        id: 'cn-south-1',
        display_name: 'Guangzhou',
        region_group: 'asia_pacific',
        region_group_en: 'Asia Pacific',
        external_endpoint: 'obs-cn.example.com',
        internal_endpoint: 'obs-cn.example.com',
        driver: 's3' as const,
        s3_url_style: 'virtual_hosted' as const,
        use_tls: true as const,
      }],
    }

    expect(normalizedStorageProviderSnapshot(candidate)).toBe(
      normalizedStorageProviderSnapshot(normalized),
    )
  })
})
