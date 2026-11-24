"use client";

import { useEffect, useState } from "react";
import { api, getKey, setKey } from "@/lib/api";
import type { Me } from "@/lib/types";

export default function LoginPage() {
  const [value, setValue] = useState("");
  const [error, setError] = useState("");
  const [existing, setExisting] = useState<string | null>(null);
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    const k = getKey();
    setExisting(k);
    if (k) {
      api<Me>("/me")
        .then(setMe)
        .catch(() => {});
    }
  }, []);

  async function submit() {
    setError("");
    try {
      const saved = getKey();
      if (saved) localStorage.removeItem("cf_api_key");
      setKey(value);
      const me = await api<Me>("/me");
      setMe(me);
      window.location.href = "/";
    } catch (e: any) {
      setError(e.message || "login failed");
    }
  }

  return (
    <div className="login">
      <h1>API key</h1>
      <p className="muted">
        Вставьте ключ пользователя copyflow (создаётся на сервере). Ключ хранится
        локально в браузере и передаётся заголовком <code>X-Api-Key</code>.
      </p>
      {existing && me && (
        <p className="muted">Текущий ключ активен: {me.display_name || `user #${me.id}`}</p>
      )}
      <input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="api key"
        onKeyDown={(e) => e.key === "Enter" && submit()}
      />
      {error && <div className="error">{error}</div>}
      <button onClick={submit}>Войти</button>
    </div>
  );
}
