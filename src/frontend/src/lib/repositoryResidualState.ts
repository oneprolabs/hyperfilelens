export type RepositoryResidualStateInput = {
  status?: string | null
  initialization_state?: string | null
}

export function isRemovedRepositoryWithResidualLocation(
  repository: RepositoryResidualStateInput,
): boolean {
  return repository.status === 'removed'
    && repository.initialization_state === 'attention_required'
}
