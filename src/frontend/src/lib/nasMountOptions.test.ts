import { describe, expect, it } from 'vitest'
import { defaultNasMountOptions, SMB_DEFAULT_MOUNT_OPTIONS, usesUtf8Iocharset } from './nasMountOptions'

describe('NAS mount option defaults', () => {
  it('uses explicit UTF-8 SMB defaults without pinning the SMB protocol version', () => {
    expect(SMB_DEFAULT_MOUNT_OPTIONS).toBe('rw,iocharset=utf8')
    expect(SMB_DEFAULT_MOUNT_OPTIONS).not.toContain('vers=')
    expect(defaultNasMountOptions('smb')).toBe(SMB_DEFAULT_MOUNT_OPTIONS)
  })

  it('does not invent NFS mount defaults', () => {
    expect(defaultNasMountOptions('nfs')).toBe('')
  })

  it('detects UTF-8 iocharset regardless of casing and whitespace', () => {
    expect(usesUtf8Iocharset('rw, iocharset = UTF8 ,vers=3.0')).toBe(true)
    expect(usesUtf8Iocharset('IOCHARSET=utf8')).toBe(true)
    expect(usesUtf8Iocharset('rw,iocharset=utf-8')).toBe(false)
    expect(usesUtf8Iocharset('rw,vers=3.0')).toBe(false)
  })
})
