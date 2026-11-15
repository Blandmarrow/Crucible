import { useQuery } from "@tanstack/react-query";
import { settingsApi } from "../api/settings";

/** GitHub's page for one release, the target of every "which version" link. */
export function releaseNotesUrl(version: string): string {
  return `https://github.com/Blandmarrow/Crucible/releases/tag/v${version}`;
}

/**
 * The running app's version, from `backend/version.py` via the health route
 * (decision 19). Shown in the sidebar under the app's name and on the Settings
 * page; both share this one query. It is fetched once: a new version means a
 * restart and a new frontend build, which the open tab only picks up on reload.
 */
export function useAppVersion(): string | undefined {
  const { data } = useQuery({
    queryKey: ["health", "version"],
    queryFn: () => settingsApi.getHealth().then((h) => h.version),
    staleTime: Infinity,
  });
  return data;
}
