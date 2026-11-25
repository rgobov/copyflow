"use client";

import { useEffect, useState } from "react";
import { api, getKey, setKey } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import type { Me } from "@/lib/types";

export default function LoginPage() {
  const { t } = useI18n();
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
      setKey(value);
      await api<Me>("/me");
      window.location.href = "/";
    } catch (e: any) {
      localStorage.removeItem("cf_api_key");
      setError(e.message || "login failed");
    }
  }

  return (
    <div className="login">
      <h1>{t("login.title")}</h1>
      <p className="muted">{t("login.description")}</p>
      {existing && me && (
        <p className="muted">
          {t("login.currentActive")} {me.display_name || `user #${me.id}`}
        </p>
      )}
      <input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder={t("login.placeholder")}
        onKeyDown={(e) => e.key === "Enter" && submit()}
      />
      {error && <div className="error">{error}</div>}
      <button onClick={submit}>{t("login.button")}</button>
    </div>
  );
}
