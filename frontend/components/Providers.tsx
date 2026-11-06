"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // Two components asking for the same job within a second share one request.
        staleTime: 1000,
        retry: 1,
        refetchOnWindowFocus: false,
      },
    },
  });
}

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(makeQueryClient);
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
