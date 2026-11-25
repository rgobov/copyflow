"use client";

import { useCallback, useEffect, useState } from "react";
import { api, getKey } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import type { Portfolio, PortfolioPosition } from "@/lib/types";

const MARKET_LABEL: Record<string, string> = {
  crypto: "Crypto", ru: "MOEX (RU)", us: "US", in: "India (NSE)",
};

export default function PortfolioPage() {
  const { t } = useI18n();
  const [pf, setPf] = useState<Portfolio | null>(null);
  const [hasKey] = useState(() => typeof window !== "undefined" && !!getKey());
  const [capital, setCapital] = useState("");
  const [ccy, setCcy] = useState("USD");
  const [allocs, setAllocs] = useState<Record<number, string>>({});
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");

  const load = useCallback(() => {
    if (!hasKey) return;
    api<Portfolio>("/portfolio")
      .then((p) => {
        setPf(p);
        setCapital(String(p.total_capital_usd || ""));
        setCcy(p.display_currency || "USD");
        setAllocs(Object.fromEntries(p.allocations.map((a) => [a.subscription_id, String(a.allocation_pct)])));
      })
      .catch((e) => setError(e.message));
  }, [hasKey]);

  useEffect(() => { load(); }, [load]);

  async function saveMe() {
    setError(""); setSaved("");
    try {
      await api("/me", { method: "PATCH", body: JSON.stringify({ total_capital_usd: Number(capital) || 0, portfolio_currency: ccy }) });
      setSaved("✓"); load();
    } catch (e: any) { setError(e.message); }
  }

  async function saveAlloc(subId: number) {
    setError(""); setSaved("");
    try {
      await api(`/subscriptions/${subId}`, { method: "PATCH", body: JSON.stringify({ allocation_pct: Number(allocs[subId]) || 0 }) });
      setSaved("✓"); load();
    } catch (e: any) { setError(e.message); }
  }

  if (!hasKey) {
    return (
      <>
        <h1>{t("portfolio.title")}</h1>
        <div className="empty">{t("dash.needsKey")} <a href="/login">{t("dash.needsKeyLink")}</a></div>
      </>
    );
  }

  const byMarket = pf ? Object.entries(pf.by_market_usd) : [];
  const maxMarket = pf ? Math.max(1, ...byMarket.map(([, v]) => v)) : 1;

  return (
    <>
      <h1>{t("portfolio.title")}</h1>
      {error && <div className="error">{error}</div>}
      {saved && <div className="muted">{saved}</div>}

      <div className="card" style={{ marginBottom: 16 }}>
        <h3>{t("portfolio.settings")}</h3>
        <div className="row">
          <span className="k">{t("portfolio.capital")}</span>
          <input style={{ width: 140 }} value={capital} onChange={(e) => setCapital(e.target.value)} />
        </div>
        <div className="row">
          <span className="k">{t("portfolio.currency")}</span>
          <select value={ccy} onChange={(e) => setCcy(e.target.value)} className="select">
            {["USD", "RUB", "EUR", "INR"].map((c) => <option key={c}>{c}</option>)}
          </select>
        </div>
        <button onClick={saveMe}>{t("portfolio.save")}</button>
      </div>

      {pf && (
        <>
          <div className="card" style={{ marginBottom: 16 }}>
            <h3>{t("portfolio.summary")}</h3>
            <div className="row">
              <span className="k">{t("portfolio.total")}</span>
              <span>{pf.equity_display?.toFixed(2)} {pf.display_currency}</span>
            </div>
            <div className="row">
              <span className="k">{t("portfolio.allocated")}</span>
              <span>{pf.allocated_pct}% ({t("portfolio.unallocated")} {pf.unallocated_pct}%)</span>
            </div>
          </div>

          <h2>{t("portfolio.allocation")}</h2>
          <div className="grid">
            {pf.allocations.map((a) => (
              <div key={a.subscription_id} className="card">
                <h3>{a.leader_name}</h3>
                <div className="row"><span className="k">sub #{a.subscription_id}</span><span>{a.mode}</span></div>
                <div className="row"><span className="k">{t("portfolio.equity")}</span><span>${a.equity_usd.toFixed(2)}</span></div>
                <div className="row">
                  <span className="k">{t("portfolio.allocation")}, %</span>
                  <input style={{ width: 80 }} value={allocs[a.subscription_id] ?? ""}
                         onChange={(e) => setAllocs((p) => ({ ...p, [a.subscription_id]: e.target.value }))} />
                </div>
                <button onClick={() => saveAlloc(a.subscription_id)}>{t("portfolio.save")}</button>
              </div>
            ))}
          </div>

          <h2>{t("portfolio.byMarket")}</h2>
          {byMarket.length === 0 ? (
            <div className="empty">{t("dash.flat")}</div>
          ) : (
            byMarket.map(([market, usd]) => (
              <div key={market} style={{ marginBottom: 8 }}>
                <div className="row"><span>{MARKET_LABEL[market] || market}</span><span>${usd.toFixed(2)}</span></div>
                <div style={{ background: "var(--border)", borderRadius: 6, height: 10 }}>
                  <div style={{ width: `${(usd / maxMarket) * 100}%`, background: "var(--accent)", height: 10, borderRadius: 6 }} />
                </div>
              </div>
            ))
          )}

          <h2>{t("dash.positions")}</h2>
          {pf.positions.length === 0 ? (
            <div className="empty">{t("dash.flat")}</div>
          ) : (
            <table>
              <thead>
                <tr><th>market</th><th>symbol</th><th>side</th><th>qty</th><th>entry</th><th>value</th></tr>
              </thead>
              <tbody>
                {pf.positions.map((p: PortfolioPosition, i: number) => (
                  <tr key={i}>
                    <td><span className="badge">{MARKET_LABEL[p.market] || p.market}</span></td>
                    <td>{p.symbol}</td>
                    <td className={p.side}>{p.side}</td>
                    <td>{p.qty.toFixed(4)}</td>
                    <td>{p.entry_price.toFixed(4)}</td>
                    <td>{p.value_native.toFixed(2)} {p.currency}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </>
  );
}
