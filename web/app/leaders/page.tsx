"use client";

import { useEffect, useState } from "react";
import { api, getKey } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import type { Leader } from "@/lib/types";

export default function LeadersPage() {
  const { t } = useI18n();
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
      <h1>{t("leaders.title")}</h1>
      {error && <div className="error">{error}</div>}
      {leaders === null ? (
        <div className="empty">{t("dash.loading")}</div>
      ) : leaders.length === 0 ? (
        <div className="empty">{t("leaders.empty")}</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>{t("leaders.colLeader")}</th><th>{t("leaders.colSource")}</th>
              <th>{t("leaders.colEquity")}</th><th>{t("leaders.colSignals")}</th>
              <th>{t("leaders.colFollowers")}</th><th>{t("leaders.colLast")}</th>
              <th>{t("leaders.colStatus")}</th><th></th>
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
                <td>{l.is_active ? <span className="badge paper">{t("leaders.active")}</span> : <span className="badge">{t("leaders.off")}</span>}</td>
                <td>
                  {following.has(l.id) ? (
                    <span className="badge paper">{t("leaders.following")}</span>
                  ) : (
                    <button
                      disabled={busy === l.id || !hasKey}
                      onClick={() => follow(l.id)}
                      title={hasKey ? t("leaders.followHint") : t("leaders.needsKeyHint")}
                    >
                      {busy === l.id ? "…" : t("leaders.follow")}
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
