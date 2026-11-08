import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { get, setSignedInEmail, signedInEmail } from "./api/client";
import type { Me } from "./api/types";

// Mock auth: the person picks a user; the email is remembered in this browser.
type Auth = { me: Me | null; loading: boolean; signIn: (email: string) => Promise<void>; signOut: () => void };

const AuthContext = createContext<Auth | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(Boolean(signedInEmail()));

  useEffect(() => {
    if (!signedInEmail()) return;
    get<Me>("/me")
      .then(setMe)
      .catch(() => setSignedInEmail(null))
      .finally(() => setLoading(false));
  }, []);

  const signIn = useCallback(async (email: string) => {
    setSignedInEmail(email);
    try {
      setMe(await get<Me>("/me"));
    } catch (e) {
      setSignedInEmail(null);
      throw e;
    }
  }, []);

  const signOut = useCallback(() => {
    setSignedInEmail(null);
    setMe(null);
  }, []);

  return <AuthContext.Provider value={{ me, loading, signIn, signOut }}>{children}</AuthContext.Provider>;
}

export function useAuth(): Auth {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
