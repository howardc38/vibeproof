type Tier = "sensitive" | "metadata" | "read"

export function useGate(tier: Tier) {
  return tier
}

export type { Tier }
