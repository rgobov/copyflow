import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "copyflow",
  description: "copy-trading platform: leaders, signals, risk",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <nav className="nav">
          <a href="/" className="brand">copyflow</a>
          <div className="navlinks">
            <a href="/">Dashboard</a>
            <a href="/leaders">Leaders</a>
            <a href="/login">API key</a>
          </div>
        </nav>
        <main className="main">{children}</main>
      </body>
    </html>
  );
}
