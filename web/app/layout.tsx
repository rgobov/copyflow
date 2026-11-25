import type { Metadata } from "next";
import { LanguageProvider, LangSwitcher } from "@/lib/i18n";
import "./globals.css";

export const metadata: Metadata = {
  title: "copyflow",
  description: "copy-trading platform: leaders, signals, risk",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <LanguageProvider>
          <nav className="nav">
            <a href="/" className="brand">copyflow</a>
            <div className="navlinks">
              <a href="/">Dashboard</a>
              <a href="/portfolio">Portfolio</a>
              <a href="/leaders">Leaders</a>
              <a href="/login">API key</a>
            </div>
            <LangSwitcher />
          </nav>
          <main className="main">{children}</main>
        </LanguageProvider>
      </body>
    </html>
  );
}
