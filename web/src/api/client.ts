// Thin client for the backend API. Base URL from VITE_API_URL (defaults to the local backend).
// Mock auth: the signed-in email travels in the X-User-Email header (or ?email= for images, downloads, sockets).
export const API_URL: string = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

const KEY = "memo-app.email";

export function signedInEmail(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function setSignedInEmail(email: string | null): void {
  try {
    if (email) localStorage.setItem(KEY, email);
    else localStorage.removeItem(KEY);
  } catch {
    /* private window: the session lasts until reload */
  }
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  const email = signedInEmail();
  if (email) headers["X-User-Email"] = email;
  let payload: BodyInit | undefined;
  if (body instanceof FormData) payload = body;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const res = await fetch(`${API_URL}${path}`, { method, headers, body: payload });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      if (j?.detail) detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export const get = <T>(path: string) => request<T>("GET", path);
export const post = <T>(path: string, body?: unknown) => request<T>("POST", path, body);

/** A URL the browser opens itself (image, download), carrying the signed-in email. */
export function withEmail(path: string): string {
  const sep = path.includes("?") ? "&" : "?";
  return `${API_URL}${path}${sep}email=${encodeURIComponent(signedInEmail() ?? "")}`;
}

export function liveUrl(memoId: string): string {
  return `${API_URL.replace(/^http/, "ws")}/memos/${memoId}/live?email=${encodeURIComponent(signedInEmail() ?? "")}`;
}
