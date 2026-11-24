// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import S3PlatformBrandIcon from './S3PlatformBrandIcon.vue'
import {
  normalizeS3StoragePlatform, s3PlatformIconUrl, s3PlatformLabelKey,
} from '../lib/s3PlatformDisplay'

describe('S3 provider brand icons', () => {
  it.each([
    ['RustFS', 'rustfs', '/cloud-platforms/rustfs.svg', 'addS3Repo.platformRustfs'],
    ['MinIO', 'minio', '/cloud-platforms/minio.png', 'addS3Repo.platformMinio'],
  ])('renders the local %s brand asset instead of a generic storage icon', (label, platform, source, key) => {
    expect(normalizeS3StoragePlatform(` ${label} `)).toBe(platform)
    expect(s3PlatformIconUrl(platform)).toBe(source)
    expect(s3PlatformLabelKey(platform)).toBe(key)
    const wrapper = mount(S3PlatformBrandIcon, {
      props: { platform, alt: label, size: 18, iconClass: 'provider-brand' },
    })
    const image = wrapper.get('img')
    expect(image.attributes('src')).toBe(source)
    expect(image.attributes('alt')).toBe(label)
    expect(image.attributes('width')).toBe('18')
    expect(image.attributes('height')).toBe('18')
    expect(image.classes()).toContain('provider-brand')
    expect(wrapper.find('svg').exists()).toBe(false)
    const asset = readFileSync(resolve(process.cwd(), 'public', source.slice(1)))
    if (source.endsWith('.png')) {
      expect(asset.subarray(0, 8)).toEqual(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))
      expect(asset.readUInt32BE(16)).toBe(32)
      expect(asset.readUInt32BE(20)).toBe(32)
    } else {
      expect(asset.toString()).toContain('viewBox=')
      expect(asset.toString()).toContain('#0062FF')
      expect(asset.toString()).not.toMatch(/<script|<foreignObject/i)
    }
    wrapper.unmount()
  })

  it('updates the selected provider and uses the same asset for the larger summary icon', async () => {
    const wrapper = mount(S3PlatformBrandIcon, { props: { platform: 'rustfs', size: 28 } })
    expect(wrapper.get('img').attributes('width')).toBe('28')
    await wrapper.setProps({ platform: 'minio', alt: 'MinIO' })
    expect(wrapper.get('img').attributes('src')).toBe('/cloud-platforms/minio.png')
    expect(wrapper.get('img').attributes('height')).toBe('28')
    wrapper.unmount()
  })

  it.each(['custom', 'other', 'unknown-provider', null])('keeps the generic fallback for %s', (platform) => {
    const wrapper = mount(S3PlatformBrandIcon, { props: { platform } })
    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.find('svg').exists()).toBe(true)
    wrapper.unmount()
  })
})
