import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import axios from "axios";
import apiClient from "@/lib/api-client";
import type { User } from "@/types";

const STORAGE_KEY = "edi_user";

function getCachedUser(): User | null {
  try {
    const s = localStorage.getItem(STORAGE_KEY);
    return s ? (JSON.parse(s) as User) : null;
  } catch {
    return null;
  }
}

async function fetchMe(): Promise<User | null> {
  try {
    const res = await apiClient.get<User>("/auth/me");
    localStorage.setItem(STORAGE_KEY, JSON.stringify(res.data));
    return res.data;
  } catch (err) {
    if (axios.isAxiosError(err) && err.response?.status === 401) {
      localStorage.removeItem(STORAGE_KEY);
      return null;
    }
    throw err;
  }
}

async function postLogin(email: string, password: string): Promise<User> {
  const res = await apiClient.post<User>("/auth/login", { email, password });
  return res.data;
}

async function postLogout(): Promise<void> {
  await apiClient.post("/auth/logout");
}

export function useAuth() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const { data: user, isLoading } = useQuery<User | null>({
    queryKey: ["auth", "me"],
    queryFn: fetchMe,
    retry: false,
    // Cached user gives instant UI, but initialDataUpdatedAt: 0 marks it stale
    // so fetchMe always revalidates against the server on mount. A dead session
    // resolves to null instead of blindly trusting localStorage.
    staleTime: 8 * 60 * 60 * 1000,
    initialData: getCachedUser,
    initialDataUpdatedAt: 0,
  });

  const loginMutation = useMutation({
    mutationFn: ({ email, password }: { email: string; password: string }) =>
      postLogin(email, password),
    onSuccess: (userData) => {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(userData));
      queryClient.setQueryData(["auth", "me"], userData);
      navigate("/", { replace: true });
    },
  });

  const logoutMutation = useMutation({
    mutationFn: postLogout,
    onSettled: () => {
      localStorage.removeItem(STORAGE_KEY);
      queryClient.clear();
      navigate("/login", { replace: true });
    },
  });

  return {
    user: user ?? null,
    isLoading,
    isAuthenticated: !!user,
    login: loginMutation.mutate,
    loginAsync: loginMutation.mutateAsync,
    loginError: loginMutation.error,
    isLoggingIn: loginMutation.isPending,
    logout: () => logoutMutation.mutate(),
  };
}
