import { useEffect, useState } from "react";
import { get, type Firm } from "../api/client";

// Step 1: the firms and their specs, read from the database through the API.
export function FirmsPage() {
  const [firms, setFirms] = useState<Firm[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    get<Firm[]>("/firms").then(setFirms).catch((e: Error) => setError(e.message));
  }, []);

  if (error) return <p className="error">Could not load firms: {error}</p>;
  if (!firms) return <p>Loading…</p>;

  return (
    <div className="firms">
      {firms.map((firm) => (
        <section key={firm.id} className="card">
          <h2>{firm.name}</h2>
          <p className="muted">Users: {firm.users.map((u) => u.email).join(", ")}</p>
          {firm.specs.map((spec) => (
            <div key={spec.id} className="spec">
              <h3>{spec.name}</h3>
              {spec.version && (
                <p className="muted">
                  v{spec.version.number} · {spec.version.label} · {spec.version.status} · page{" "}
                  {spec.version.page.join(" × ")} in
                </p>
              )}
              <ul className="counts">
                {Object.entries(spec.counts).map(([k, v]) => (
                  <li key={k}>
                    <strong>{v}</strong> {k}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </section>
      ))}
    </div>
  );
}
