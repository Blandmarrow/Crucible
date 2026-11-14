import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { dictionaryApi } from "../api/dictionary";
import { DICTIONARY_STATUS_KEY, dictionaryScope } from "../constants/queryKeys";
import type { DictionaryStatus } from "../types";

const BUSY = new Set(["downloading", "converting"]);

export const dictionaryBusy = (s: DictionaryStatus | undefined) => !!s && BUSY.has(s.state);

/**
 * The synonym dictionary's install state (#52), shared by Settings and the caption
 * editor's synonym card. The install runs on the server outside the job queue, so
 * there is no SSE stream: this polls once a second while it is downloading or
 * converting, and not at all otherwise.
 */
export function useDictionaryStatus(enabled = true) {
  return useQuery({
    queryKey: DICTIONARY_STATUS_KEY,
    queryFn: dictionaryApi.status,
    enabled,
    staleTime: 30_000,
    refetchInterval: (q) => (dictionaryBusy(q.state.data) ? 1000 : false),
  });
}

/** Start the install (or join the running one) and remove the dictionary. Both write
 *  the returned status straight into the cache, then refresh everything dictionary. */
export function useDictionaryActions() {
  const qc = useQueryClient();
  const settle = (status: DictionaryStatus) => {
    qc.setQueryData(DICTIONARY_STATUS_KEY, status);
    void qc.invalidateQueries({ queryKey: dictionaryScope });
  };
  const install = useMutation({ mutationFn: dictionaryApi.install, onSuccess: settle });
  const remove = useMutation({ mutationFn: dictionaryApi.remove, onSuccess: settle });
  return { install, remove };
}
