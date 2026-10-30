export const queryKeys = {
  session: ['session'] as const,
  repositories: ['repositories'] as const,
  branches: (repositoryId: string) => ['repositories', repositoryId, 'branches'] as const,
  runs: ['runs'] as const,
  run: (runId: string) => ['runs', runId] as const,
  // version = updatedAt прогона: результаты перезапрашиваются, когда сервер что-то изменил.
  results: (runId: string, version: string) => ['runs', runId, 'results', version] as const,
}
