import { useQuery } from "@tanstack/react-query";
import client from "../api/client";

interface CpuRamStats {
  cpu_pct: number;
  ram_used_mb: number;
  ram_total_mb: number;
}

export function useCpuRamStats() {
  const { data } = useQuery<CpuRamStats>({
    queryKey: ["cpu-ram-stats"],
    queryFn: () => client.get<CpuRamStats>("/system/cpu-ram").then((r) => r.data),
    refetchInterval: 5000,
    staleTime: 4000,
    retry: false,
  });
  return data ?? null;
}
