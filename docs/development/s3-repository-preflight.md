# S3 repository creation preflight

All S3 repository creation requests run server-side preflight before inserting
Repository, Credential, location claims, or initialization tasks. The form stays
open on a preflight error. Successful requests retain the asynchronous `creating`
response and existing initialization recovery behavior.

Sequence:

1. Check quota and existing repository prefix overlap.
2. For new-bucket mode, require HEAD to confirm absence before creating the bucket.
   An existing or inaccessible bucket must not proceed to probe or repository creation.
3. Resolve addressing style and list the target prefix.
4. Remove recognized probe residue, then require an empty prefix.
5. Upload the fixed probe with overwrite prevention, delete it, and recheck emptiness.
6. Persist the repository with its original bucket mode, and enqueue initialization.
   After validation, server-set `config.s3_bucket_prepared` prevents duplicate
   creation by the worker. Keeping `s3_bucket_mode=new` preserves cleanup eligibility
   for genuinely new buckets. No schema changes or pre-validation records are used.

The reserved key is `<prefix>.hfl-repository-probe-550e8400-e29b-41d4-a716-446655440000.tmp`.
Only exact metadata (`hfl-purpose=repository-preflight`, `hfl-probe-version=1`)
and the expected content length authorize residue deletion. HEAD reads metadata;
preflight never downloads probe contents. Unknown objects, including a reserved
key with different metadata, stop creation. This verifies listing, writing and
deletion; it does not prove GET access. Version IDs returned by PUT/HEAD are
passed to DELETE to avoid leaving probe versions or creating delete markers.

A PostgreSQL session advisory lock serializes application create requests for the
same normalized endpoint and bucket, including overlapping prefixes. Conditional
PUT also prevents overwriting a probe created outside that lock. Initialization
retains its existing physical-location checks; preflight cannot make operations
in an external object store and the database one atomic transaction.

No temporary database records or new schema are needed. If preparation fails after
this request successfully created a bucket, attempt to delete that bucket only if
it is empty. Existing buckets and nonempty buckets are never removed. Rollback
failure is included in the error response. Ambiguous bucket-creation results must
be checked at the provider; no automatic adoption occurs on retry.

The default frontend prefix uses local time, for example `hfl6I09163025/` for
September 9, 2026 at 16:30:25. Its year digit repeats every ten years; storage checks,
rather than the name generator, decide whether a location can be used.
