import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: '白话量化 · 策略监控台',
  description: '查看 BulletTrade 策略资金、持仓、委托成交与运行日志。',
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
