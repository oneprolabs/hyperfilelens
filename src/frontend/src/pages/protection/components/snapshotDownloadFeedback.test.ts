import { describe, expect, it } from 'vitest'
import {
  SNAPSHOT_DOWNLOAD_SIZE_LIMIT_EXCEEDED_CODE,
  snapshotDownloadSizeLimit,
} from './snapshotDownloadFeedback'

describe('snapshot download feedback', () => {
  it('reads structured download-limit metadata', () => {
    expect(snapshotDownloadSizeLimit({
      status: 400,
      errorCode: SNAPSHOT_DOWNLOAD_SIZE_LIMIT_EXCEEDED_CODE,
      message: 'Snapshot download exceeds the size limit.',
      meta: {
        selected_size_bytes: 9_941_405_873,
        max_size_bytes: 209_715_200,
      },
    })).toEqual({
      selectedBytes: 9_941_405_873,
      limitBytes: 209_715_200,
    })
  })

  it('keeps old backend messages readable during rolling upgrades', () => {
    expect(snapshotDownloadSizeLimit(new Error(
      'Selected data is 9941405873 bytes, exceeding the 209715200-byte download limit. Select fewer items.',
    ))).toEqual({
      selectedBytes: 9_941_405_873,
      limitBytes: 209_715_200,
    })
  })

  it('ignores unrelated failures', () => {
    expect(snapshotDownloadSizeLimit(new Error('Connection failed'))).toBeNull()
  })
})
