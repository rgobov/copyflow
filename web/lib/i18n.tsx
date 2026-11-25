"use client";

import { createContext, useContext, useEffect, useState } from "react";

export type Lang = "en" | "ru";

const en: Record<string, string> = {
  "nav.dashboard": "Dashboard",
  "nav.leaders": "Leaders",
  "nav.apiKey": "API key",

  "login.title": "API key",
  "login.description":
    "Paste your copyflow user key (created on the server). It is stored locally in your browser and sent as an X-Api-Key header.",
  "login.currentActive": "Current key is active:",
  "login.placeholder": "api key",
  "login.button": "Sign in",

  "dash.title": "Dashboard",
  "dash.needsKey": "Enter your API key on the",
  "dash.needsKeyLink": "login page",
  "dash.loading": "loading…",
  "dash.noSubs": "no subscriptions yet — create one via API or the Follow button",
  "dash.sub": "subscription",
  "dash.leader": "leader",
  "dash.executor": "executor",
  "dash.delivery": "delivery",
  "dash.equity": "equity",
  "dash.peak": "peak equity",
  "dash.limits": "limits",
  "dash.fees": "fees accrued",
  "dash.dayLoss": "day",
  "dash.drawdown": "dd",
  "dash.positions": "Positions",
  "dash.flat": "flat",
  "dash.fills": "Fills",
  "dash.noFills": "no fills yet",
  "dash.tape": "Signal tape",
  "dash.noSignals": "no signals yet",
  "dash.feePlatform": "platform",
  "dash.feeLeader": "leader",

  "leaders.title": "Leaders",
  "leaders.empty": "no leaders yet",
  "leaders.colLeader": "leader",
  "leaders.colSource": "source",
  "leaders.colEquity": "equity",
  "leaders.colSignals": "signals",
  "leaders.colFollowers": "followers",
  "leaders.colLast": "last signal",
  "leaders.colStatus": "status",
  "leaders.follow": "Follow",
  "leaders.followHint": "subscribe (paper, one click)",
  "leaders.needsKeyHint": "enter your API key first",
  "leaders.following": "following ✓",
  "leaders.active": "active",
  "leaders.off": "off",
};

const ru: Record<string, string> = {
  "nav.dashboard": "Дашборд",
  "nav.leaders": "Лидеры",
  "nav.apiKey": "API-ключ",

  "login.title": "API-ключ",
  "login.description":
    "Вставьте ключ пользователя copyflow (создаётся на сервере). Ключ хранится локально в браузере и передаётся заголовком X-Api-Key.",
  "login.currentActive": "Текущий ключ активен:",
  "login.placeholder": "api-ключ",
  "login.button": "Войти",

  "dash.title": "Дашборд",
  "dash.needsKey": "Введите API-ключ на странице",
  "dash.needsKeyLink": "входа",
  "dash.loading": "загрузка…",
  "dash.noSubs": "нет подписок — создайте через API или кнопку Follow",
  "dash.sub": "подписка",
  "dash.leader": "лидер",
  "dash.executor": "исполнитель",
  "dash.delivery": "доставка",
  "dash.equity": "эквити",
  "dash.peak": "пик эквити",
  "dash.limits": "лимиты",
  "dash.fees": "начислено комиссий",
  "dash.dayLoss": "день",
  "dash.drawdown": "просадка",
  "dash.positions": "Позиции",
  "dash.flat": "нет позиций",
  "dash.fills": "Сделки",
  "dash.noFills": "пока нет сделок",
  "dash.tape": "Лента сигналов",
  "dash.noSignals": "пока нет сигналов",
  "dash.feePlatform": "платформа",
  "dash.feeLeader": "лидер",

  "leaders.title": "Лидеры",
  "leaders.empty": "пока нет лидеров",
  "leaders.colLeader": "лидер",
  "leaders.colSource": "источник",
  "leaders.colEquity": "эквити",
  "leaders.colSignals": "сигналы",
  "leaders.colFollowers": "подписчики",
  "leaders.colLast": "последний сигнал",
  "leaders.colStatus": "статус",
  "leaders.follow": "Подписаться",
  "leaders.followHint": "подписаться (paper, 1 клик)",
  "leaders.needsKeyHint": "сначала введите API-ключ",
  "leaders.following": "вы подписаны ✓",
  "leaders.active": "активен",
  "leaders.off": "выключен",
};

const dicts: Record<Lang, Record<string, string>> = { en, ru };

type I18n = { lang: Lang; setLang: (l: Lang) => void; t: (key: string) => string };

const I18nContext = createContext<I18n>({ lang: "en", setLang: () => {}, t: (k) => k });

export function LanguageProvider({ children }: { children: React.ReactNode }) {
  const [lang, setLang] = useState<Lang>("en");

  useEffect(() => {
    const saved = localStorage.getItem("cf_lang") as Lang | null;
    const detected: Lang = navigator.language?.toLowerCase().startsWith("ru") ? "ru" : "en";
    setLang(saved && saved in dicts ? saved : detected);
  }, []);

  function change(l: Lang) {
    setLang(l);
    localStorage.setItem("cf_lang", l);
  }

  const t = (key: string) => dicts[lang][key] ?? dicts.en[key] ?? key;
  return <I18nContext.Provider value={{ lang, setLang: change, t }}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  return useContext(I18nContext);
}

export function LangSwitcher() {
  const { lang, setLang } = useI18n();
  return (
    <span className="langswitch">
      <button className={`lang ${lang === "en" ? "on" : ""}`} onClick={() => setLang("en")}>EN</button>
      <button className={`lang ${lang === "ru" ? "on" : ""}`} onClick={() => setLang("ru")}>RU</button>
    </span>
  );
}
