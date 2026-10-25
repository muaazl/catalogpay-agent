import { createContext, useContext, type ReactNode } from "react";

import { api as defaultClient, type ApiClient } from "./client";

const ApiContext = createContext<ApiClient>(defaultClient);

export function ApiProvider({ client, children }: { client: ApiClient; children: ReactNode }) {
  return <ApiContext.Provider value={client}>{children}</ApiContext.Provider>;
}

export function useApi(): ApiClient {
  return useContext(ApiContext);
}
