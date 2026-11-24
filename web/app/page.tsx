"use client";

import { useEffect, useState } from "react";
import { api, getKey } from "@/lib/api";
import type { Fill, Signal, Subscription } from "@/lib/types";

type Position = { symbol: string; side: string; qty: number; entry_price: number };

export default function DashboardPage() {
  const [subs, setSubs] = useState<Subscription[] | null>(null);
  const [fills, setFills] = useState<Fill[]>([]);
  const [signals, setSignals] = useState<Signal[]>([]);
  const [positions, setPositions] = useState<Record<number, Position[]>>({});
  const [hasKey] = useState(() => typeof window !== "undefined" && !!getKey());

  useEffect(() => {
    if (!hasKey) return;
    api<Subscription[]>("/subscriptions")
      .then(setSubs)
      .catch(() => setSubs([]));
    api<Fill[]>("/fills?limit=10").then(setFills).catch(() => {});
    api<Signal[]>("/signals?limit=10").then(setSignals).catch(() => {});
  }, [hasKey]);

  useEffect(() => {
    if (!subs) return;
    subs.forEach((s) => {
      api<{ positions: Position[] }>(`/positions?subscription_id=${s.id}`)
        .then((r) => setPositions((p) => ({ ...p, [s.id]: r.positions })))
        .catch(() => {});
    });
  }, [subs]);

  if (!hasKey) {
    return (
      <>
        <h1>Dashboard</h1>
        <div className="empty">Введите API key на странице <a href="/login">login</a></div>
      </>
    );
  }

  return (
    <>
      <h1>Dashboard</h1>

      <h2>Subscriptions</h2>
      {subs === null ? (
        <div className="empty">loading…</div>
      ) : subs.length === 0 ? (
        <div className="empty">нет подписок — создайте через API</div>
      ) : (
        <div className="grid">
          {subs.map((s) => (
            <div key={s.id} className="card">
              <h3>
                subscription #{s.id} · leader #{s.leader_id}
              </h3>
              <div className="row"><span className="k">executor</span><span>{s.executor} ({s.mode})</span></div>
              <div className="row"><span className="k">delivery</span><span>{s.delivery}</span></div>
              <div className="row"><span className="k">equity</span><span>${s.equity_usd.toFixed(2)}</span></div>
              <div className="row"><span className="k">peak equity</span><span>${s.peak_equity.toFixed(2)}</span></div>
              <div className="row">
                <span className="k">limits</span>
                <span>
                  day -{s.daily_loss_limit_pct}% / dd {s.max_drawdown_pct}%
                </span>
              </div>
              <h2>Positions</h2>
              {(positions[s.id] || []).length === 0 ? (
                <div className="muted">flat</div>
              ) : (
                (positions[s.id] || []).map((p) => (
                  <div key={p.symbol} className="row">
                    <span className={p.side}>{p.side.toUpperCase()} {p.symbol}</span>
                    <span>{p.qty.toFixed(6)} @ {p.entry_price.toFixed(4)}</span>
                  </div>
                ))
              )}
            </div>
          ))}
        </div>
      )}

      <h2>Fills</h2>
      {fills.length === 0 ? (
        <div className="empty">no fills yet</div>
      ) : (
        <table>
          <thead>
            <tr><th>sub</th><th>symbol</th><th>side</th><th>qty</th><th>price</th><th>fee</th></tr>
          </thead>
          <tbody>
            {fills.map((f) => (
              <tr key={f.id}>
                <td>#{f.subscription_id}</td>
                <td>{f.symbol}</td>
                <td className={f.side}>{f.side}</td>
                <td>{f.qty.toFixed(6)}</td>
                <td>{f.price.toFixed(4)}</td>
                <td>${f.fee_usd.toFixed(3)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h2>Signal tape</h2>
      {signals.length === 0 ? (
        <div className="empty">no signals yet</div>
      ) : (
        <table>
          <thead>
            <tr><th>leader</th><th>action</th><th>side</th><th>symbol</th><th>size</th><th>ref price</th></tr>
          </thead>
          <tbody>
            {signals.map((s) => (
              <tr key={s.signal_id}>
                <td>{s.leader_name}</td>
                <td>{s.action}</td>
                <td className={s.side}>{s.side}</td>
                <td>{s.symbol}</td>
                <td>{s.size_frac !== null ? `${(s.size_frac * 100).toFixed(0)}%` : s.size_abs_usd ? `$${s.size_abs_usd}` : "—"}</td>
                <td>{s.ref_price ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
