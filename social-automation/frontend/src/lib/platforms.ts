export function pruneIdsToKnown<T extends string>(ids: T[], knownIds: ReadonlySet<string>): T[] {
  let changed = false
  const next: T[] = []

  for (const id of ids) {
    if (knownIds.has(id)) next.push(id)
    else changed = true
  }

  return changed ? next : ids
}

