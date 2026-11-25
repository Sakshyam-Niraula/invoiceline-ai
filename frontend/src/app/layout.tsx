import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "InvoiceLine AI",
  description: "AI-powered invoice extraction, line-item processing and validation platform",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
