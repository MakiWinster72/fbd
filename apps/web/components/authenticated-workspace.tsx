"use client";

import { useEffect, useState } from "react";
import { ChatWorkspace } from "@/components/chat-workspace";
import { useLanguage } from "@/components/language-provider";
import {
  getCurrentUser,
  listKnowledgeBases,
  logout,
  type CurrentUser,
  type KnowledgeBase,
} from "@/lib/api";

export function AuthenticatedWorkspace({
  assistantIconUrl,
}: {
  assistantIconUrl?: string;
}) {
  const { t, locale } = useLanguage();
  const [user, setUser] = useState<CurrentUser | null | undefined>(undefined);
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([getCurrentUser(), listKnowledgeBases()])
      .then(([current, accessibleKnowledgeBases]) => {
        if (!current) {
          window.location.replace("/login?next=/");
          return;
        }
        setUser(current);
        setKnowledgeBases(accessibleKnowledgeBases);
      })
      .catch((reason) => {
        const message =
          reason instanceof Error ? reason.message : "无法检查登录状态";
        if (message === "请先登录") {
          window.location.replace("/login?next=/");
          return;
        }
        setError(message);
      });
  }, []);

  if (error) {
    return (
      <main id="main-content" tabIndex={-1} className="auth-status">
        <strong>{t("无法连接认证服务")}</strong>
        <p>{t(error)}</p>
      </main>
    );
  }
  if (!user) {
    return (
      <main id="main-content" tabIndex={-1} className="auth-status">
        <span className="auth-spinner" />
        <p>{t("正在验证登录状态…")}</p>
      </main>
    );
  }
  if (!knowledgeBases.length) {
    return (
      <main id="main-content" tabIndex={-1} className="empty-access-shell">
        <div className="login-seal" aria-hidden="true">
          知
        </div>
        <p className="section-kicker">{t("访问权限")}</p>
        <h1>{t("尚未分配知识库")}</h1>
        <a href="/account">{locale === "en" ? "User center" : "用户中心"}</a>
        <p>
          {t(
            "当前账号可以登录，但没有任何知识库访问权限。请联系管理员完成授权。",
          )}
        </p>
        <button
          type="button"
          onClick={async () => {
            await logout();
            window.location.assign("/login");
          }}
        >
          {t("退出当前账号")}
        </button>
      </main>
    );
  }
  return (
    <ChatWorkspace
      key={user.id}
      currentUser={user}
      knowledgeBases={knowledgeBases}
      assistantIconUrl={assistantIconUrl}
    />
  );
}
