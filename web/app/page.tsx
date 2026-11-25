"use client";

import { useEffect, useState } from "react";
import { api, getKey } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import type { Fill, Signal, Subscription } from "@/lib/types";

type Position = { symbol: string; side: string; qty: number; entry_price: number };

export default function DashboardPage() {
  const { t } = useI18n();
  const [subs, setSubs] = useState<Subscription[] | null>(null);
  const [fills, setFills] = useState<Fill[]>([]);
  const [signals, setSignals] = useState<Signal[]>([]);
  const [positions, setPositions] = useState<Record<number, Position[]>>({});
  const [billing, setBilling] = useState<Record<number, BillingSummary>>({});
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
      if (s.mode === "live") {
        api<BillingSummary>(`/billing?subscription_id=${s.id}`)
          .then((b) => setBilling((prev) => ({ ...prev, [s.id]: b })))
          .catch(() => {});
      }
    });
  }, [subs]);

  if (!hasKey) {
    return (
      <>
        <h1>{t("dash.title")}</h1>
        <div className="empty">
          {t("dash.needsKey")} <a href="/login">{t("dash.needsKeyLink")}</a>
        </div>
      </>
    );
  }

  return (
    <>
      <h1>{t("dash.title")}</h1>

      <h2>{t("dash.sub")}</h2>
      {subs === null ? (
        <div className="empty">{t("dash.loading")}</div>
      ) : subs.length === 0 ? (
        <div className="empty">{t("dash.noSubs")}</div>
      ) : (
        <div className="grid">
          {subs.map((s) => (
            <div key={s.id} className="card">
              <h3>
                {t("dash.sub")} #{s.id} · {t("dash.leader")} #{s.leader_id}
              </h3>
              <div className="row"><span className="k">{t("dash.executor")}</span><span>{s.executor} ({s.mode})</span></div>
              <div className="row"><span className="k">{t("dash.delivery")}</span><span>{s.delivery}</span></div>
              <div className="row"><span className="k">{t("dash.equity")}</span><span>${s.equity_usd.toFixed(2)}</span></div>
              <div className="row"><span className="k">{t("dash.peak")}</span><span>${s.peak_equity.toFixed(2)}</span></div>
              <div className="row">
                <span className="k">{t("dash.limits")}</span>
                <span>
                  {t("dash.dayLoss")} -{s.daily_loss_limit_pct}% / {t("dash.drawdown")} {s.max_drawdown_pct}%
                </span>
              </div>
              {s.mode === "live" && billing[s.id] && (
                <div className="row">
                  <span className="k">{t("dash.fees")}</span>
                  <span>
                    {t("dash.feePlatform")} ${billing[s.id].accrued_platform.toFixed(2)} ·{" "}
                    {t("dash.feeLeader")} ${billing[s.id].accrued_leader.toFixed(2)}
                  </span>
                </div>
              )}
              <h2>{t("dash.positions")}</h2>
              {(positions[s.id] || []).length === 0 ? (
                <div className="muted">{t("dash.flat")}</div>
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

      <h2>{t("dash.fills")}</h2>
      {fills.length === 0 ? (
        <div className="empty">{t("dash.noFills")}</div>
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

      <h2>{t("dash.tape")}</h2>
      {signals.length === 0 ? (
        <div className="empty">{t("dash.noSignals")}</div>
      ) : (
        <table>
          <thead>
            <tr><th>{t("dash.leader")}</th><th>action</th><th>side</th><th>symbol</th><th>size</th><th>ref price</th></tr>
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

type BillingSummary = {
  accrued_platform: number;
  accrued_leader: number;
};
