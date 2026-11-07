// Thin client for the backend API. Base URL from VITE_API_URL (defaults to the local backend).
export const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} on ${path}`);
  return res.json() as Promise<T>;
}

export type SpecSummary = {
  id: string;
  name: string;
  version: { id: string; number: number; label: string | null; status: string; page: [number, number] } | null;
  counts: Record<string, number>;
};

export type Firm = {
  id: string;
  name: string;
  users: { id: string; email: string }[];
  specs: SpecSummary[];
};
