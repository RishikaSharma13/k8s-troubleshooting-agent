import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = { title: "AI Kubernetes Agent", description: "Kubernetes troubleshooting with AI" };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
