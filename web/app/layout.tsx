import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "TaskFlow",
  description: "Website tasks extracted from email, sorted by urgency and impact",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="min-h-screen">{children}</body>
    </html>
  );
}
