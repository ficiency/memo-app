import { useEffect, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { get } from "../api/client";
import type { MockUser } from "../api/types";
import { useAuth } from "../auth";

// Mock sign-in: pick a person. Each firm is its own account and only sees its own work.
export function SignInPage() {
  const { me, signIn } = useAuth();
  const navigate = useNavigate();
  const [users, setUsers] = useState<MockUser[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    get<MockUser[]>("/users").then(setUsers).catch((e: Error) => setError(e.message));
  }, []);

  if (me) return <Navigate to="/memos" replace />;

  const firms = [...new Set(users.map((u) => u.firm))];
  const choose = async (email: string) => {
    try {
      await signIn(email);
      navigate("/memos");
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div className="signin">
      <div className="signin-card">
        <span className="brand-mark big">M</span>
        <h1>One memo, every firm</h1>
        <p className="muted">Choose who you are. Each firm is a separate account.</p>
        {error && <p className="error">{error}</p>}
        <div className="signin-firms">
          {firms.map((firm) => (
            <div key={firm} className="signin-firm">
              <h2>{firm}</h2>
              {users
                .filter((u) => u.firm === firm)
                .map((u) => (
                  <button key={u.email} className="signin-user" onClick={() => choose(u.email)}>
                    <span className="avatar">{u.name.slice(0, 1)}</span>
                    <span>
                      <strong>{u.name}</strong>
                      <span className="muted small">{u.email}</span>
                    </span>
                  </button>
                ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
