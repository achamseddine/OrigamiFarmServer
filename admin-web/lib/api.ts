// Empty means same-origin: in production the API container serves this
// console itself, so /platform/v1/... resolves without CORS or a baked-in
// hostname. Only a split dev setup (next dev on :3000, API on :8000) needs
// NEXT_PUBLIC_API_BASE_URL, and it is read at build time.
const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "";

export class ApiError extends Error {
  code: string;
  status: number;

  constructor(code: string, message: string, status: number) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem("origami_token");
}

export function setToken(token: string) {
  window.localStorage.setItem("origami_token", token);
}

export function clearToken() {
  window.localStorage.removeItem("origami_token");
}

export async function apiFetch<T>(
  path: string,
  options: { method?: string; body?: unknown; headers?: Record<string, string> } = {}
): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(options.headers || {}),
  };

  const res = await fetch(`${API_BASE}${path}`, {
    method: options.method || "GET",
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  });

  if (res.status === 204) {
    return undefined as T;
  }

  const data = await res.json().catch(() => null);

  if (!res.ok) {
    // A session that expired mid-visit would otherwise leave every panel
    // showing its own 401. Send the whole page back to sign-in once —
    // except for the calls whose 401 is an answer about the credentials
    // just typed, not about the session: sign-in ("wrong password"),
    // change-password ("wrong current password"), and the invitation
    // pages, where the person holding an expired link has no admin
    // session to send back to. Those belong to their forms.
    const isCredentialCheck =
      path.startsWith("/platform/v1/auth/login") ||
      path.startsWith("/platform/v1/auth/change-password") ||
      path.startsWith("/api/v1/auth/invitation/");
    if (res.status === 401 && !isCredentialCheck && typeof window !== "undefined") {
      clearToken();
      if (!window.location.pathname.startsWith("/login")) {
        window.location.href = "/login/";
      }
    }
    const code = data?.error?.code || "UNKNOWN_ERROR";
    const message = data?.error?.message || res.statusText;
    throw new ApiError(code, message, res.status);
  }

  return data as T;
}
