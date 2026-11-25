"use client";

import { useEffect, useState } from "react";
import { api, getKey } from "@/lib/api";
import type { Leader } from "@/lib/types";

export default function LeadersPage() {
  const [leaders, setLeaders] = useState<Leader[] | null>(null);
  const [error, setError] = useState("");
  const [following, setFollowing] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState<number | null>(null);
  const [hasKey] = useState(() => typeof window !== "undefined" && !!getKey());

  useEffect(() => {
    api<Leader[]>("/leaders")
      .then(setLeaders)
      .catch((e) => setError(e.message));
    if (hasKey) {
      api<{ leader_id: number }[]>("/subscriptions")
        .then((subs) => setFollowing(new Set(subs.map((s) => s.leader_id))))
        .catch(() => {});
    }
  }, [hasKey]);

  async function follow(leaderId: number) {
    setBusy(leaderId);
    try {
      await api("/subscriptions", {
        method: "POST",
        body: JSON.stringify({ leader_id: leaderId, executor: "paper", mode: "paper" }),
      });
      setFollowing((prev) => new Set(prev).add(leaderId));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  }

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
              <th></th>
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
                <td>
                  {following.has(l.id) ? (
                    <span className="badge paper">following ✓</span>
                  ) : (
                    <button
                      disabled={busy === l.id || !hasKey}
                      onClick={() => follow(l.id)}
                      title={hasKey ? "подписаться (paper, 1 клик)" : "сначала введите API key"}
                    >
                      {busy === l.id ? "…" : "Follow"}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
