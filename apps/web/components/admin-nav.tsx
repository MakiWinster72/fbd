"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useLanguage } from "@/components/language-provider";

const sections = [
  { href: "/admin/knowledge-bases", label: "知识库管理" },
  { href: "/admin/documents", label: "文件导入" },
  { href: "/admin/models", label: "模型配置" },
  { href: "/admin/users", label: "用户管理" },
];

export function AdminNav() {
  const pathname = usePathname();
  const { t } = useLanguage();
  return <nav className="admin-navigation" aria-label={t("管理导航")}>
    {sections.map(({ href, label }) => <Link key={href} href={href} aria-current={pathname === href ? "page" : undefined}>{t(label)}</Link>)}
  </nav>;
}
