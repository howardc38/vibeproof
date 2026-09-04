type Tier = "sensitive" | "metadata" | "read"

export function useGate(tier: Tier) {
  return tier
}

// The re-export list form. A module whose only publication of a name is this
// line published nothing by that name until `_TS_EXPORT_LIST` learned the
// optional `type`, and every importer of `Tier` was reported.
export type { Tier }
