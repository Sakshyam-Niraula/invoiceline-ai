"use client";

import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useState } from "react";
import { api, setStoredToken } from "@/lib/api";
import type { AuthSession, MeResponse } from "@/types/api";

interface AuthContextValue {
  me: MeResponse | null;
  isLoading: boolean;
  setSession: (session: AuthSession) => void;
  logout: () => Promise<void>;
  refetchMe: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function InnerAuthProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();

  const { data, isLoading, refetch } = useQuery({
    queryKey: ["auth", "me"],
    queryFn: async () => {
      try {
        return await api.me();
      } catch {
        return null;
      }
    },
    retry: false,
    staleTime: 60_000,
  });

  const setSession = useCallback(
    (session: AuthSession) => {
      setStoredToken(session.access_token);
      queryClient.invalidateQueries({ queryKey: ["auth", "me"] });
    },
    [queryClient],
  );

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      setStoredToken(null);
      queryClient.setQueryData(["auth", "me"], null);
      queryClient.clear();
    }
  }, [queryClient]);

  const refetchMe = useCallback(async () => {
    await refetch();
  }, [refetch]);

  return (
    <AuthContext.Provider
      value={{
        me: data ?? null,
        isLoading,
        setSession,
        logout,
        refetchMe,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function AppProviders({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            refetchOnWindowFocus: false,
            retry: 1,
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <InnerAuthProvider>{children}</InnerAuthProvider>
    </QueryClientProvider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used inside AppProviders");
  }
  return ctx;
}
