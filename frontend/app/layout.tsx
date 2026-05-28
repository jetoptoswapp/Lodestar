import type { Metadata } from "next";
import { Fraunces, Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

// Editorial display × technical body × monospace details
const fraunces = Fraunces({
  variable: "--font-display",
  subsets: ["latin"],
  weight: ["500", "600", "700"],
  style: ["normal", "italic"],
  display: "swap",
});

const geistSans = Geist({
  variable: "--font-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Forge — Requirement Forging Platform",
  description: "From idea to delivery — plugin-first AI requirement engineering",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="zh-Hant"
      className={`${fraunces.variable} ${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="h-full">{children}</body>
    </html>
  );
}
