"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Leader } from "@/lib/types";

export default function LeadersPage() {
  const [leaders, setLeaders] = useState<Leader[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api<Leader[]>("/leaders")
      .then(setLeaders)
      .catch((e) => setError(e.message));
  }, []);

  return (
    <>
      <h1>Leaders</h1>
      {error && <div className="error">{error}</div>}
      {leaders === null ? (
        <div className="empty">loading…</div>
      ) : leaders.length === 0 ? (
        <div className="empty">пока нет лидеров</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>leader</th><th>source</th><th>equity</th>
              <th>signals</th><th>followers</th><th>last signal</th><th>status</th>
            </tr>
          </thead>
          <tbody>
            {leaders.map((l) => (
              <tr key={l.id}>
                <td>{l.name}</td>
                <td><span className="badge">{l.source}</span></td>
                <td>${l.equity_usd.toLocaleString()}</td>
                <td>{l.signal_count}</td>
                <td>{l.followers}</td>
                <td className="muted">
                  {l.last_signal_at ? new Date(l.last_signal_at + "Z").toLocaleString() : "—"}
                </td>
                <td>{l.is_active ? <span className="badge paper">active</span> : <span className="badge">off</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
