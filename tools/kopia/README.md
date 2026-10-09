# Managed Kopia snapshot metrics

HyperFileLens builds Kopia with the patches listed in `common.sh`. Do not edit
`build/kopia/source` as product source: preparation recreates that cache.

## Progress

The `hfl-json` schema keeps logical processing and repository upload counters
separate. Optional hashed/cached byte and entry counts distinguish missing
metrics from measured zero. Processed entries include symlinks; estimated file
counts and the final manifest summary use their own definitions. No source
paths are collected for this feature.

The Agent coalesces ordinary backup samples every five seconds. Real phase
changes and terminal results are sent promptly. Five-second liveness is
independent of substantive progress; sequences alone do not prove work.

## Creation-session content statistics

`hflCreateStats` is persisted inside the snapshot manifest. Version 1 uses basis
`creation_session_data_v1`: data contents newly admitted by the direct writer,
including symlink targets but excluding prefixed metadata, indirect indexes,
pack framing/padding and repository management overhead. `OriginalBytes` and
`PackedBytes` are lengths of the same admitted contents. Packed lengths use
Kopia's stored content representation, including per-content encryption
overhead; they are not a measurement of compressor output alone. Count only after the
second, lock-protected deduplication check. Upload retries do not increment the
counters again, and intermediate checkpoint flushes do not reset them.

The CLI captures a counter delta for each source in a multi-source invocation.
Statistics are provisional until the command and enclosing flush succeed.
They are not a billing total, not bytes hashed, and not the `OnUpload` counter.
Concurrent independent sessions may each admit a duplicate; this is a
session-local metric, not a globally coordinated storage-growth measurement.

Only direct writers supporting content-layer compression produce version 1
statistics. Old formats and remote repository servers return unavailable
metrics without preventing backup. Reconciliation reads the saved manifest;
it does not create another snapshot or calculate full source-history stats.

Legacy `snapshot list --storage-stats` values are source-history references and
remain readable. They are not equivalent to creation-session values, especially
when different sources share contents. Normal managed backups never run that
command after creating a snapshot and never fall back to it for old binaries.
