import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Brickforge — AI-designed LEGO models",
  description:
    "Describe what you want. We design a buildable LEGO model, with step-by-step instructions and a priced parts list.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
