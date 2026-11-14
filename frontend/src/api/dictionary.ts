import client from "./client";
import type { DictionaryStatus, SynonymsResponse } from "../types";

/** The offline synonym dictionary (#52). Nothing here leaves the machine except
 *  `install`, which has the server fetch the WordNet archive once. */
export const dictionaryApi = {
  status: () => client.get<DictionaryStatus>("/dictionary/status").then((r) => r.data),
  install: () => client.post<DictionaryStatus>("/dictionary/install").then((r) => r.data),
  remove: () => client.delete<DictionaryStatus>("/dictionary").then((r) => r.data),
  synonyms: (word: string, signal?: AbortSignal) =>
    client.get<SynonymsResponse>("/dictionary/synonyms", { params: { word }, signal }).then((r) => r.data),
};
