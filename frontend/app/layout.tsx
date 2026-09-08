import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = {title: "Knowledge Fusion", description: "Your private, evidence-backed knowledge workspace"};
export default function RootLayout({children}: Readonly<{children: React.ReactNode}>) {
  return <html lang="en"><body>{children}</body></html>;
}
