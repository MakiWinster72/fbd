"use client";

import { FormEvent, useEffect, useState } from "react";
import { getCurrentUser, login } from "@/lib/api";
import { useLanguage } from "@/components/language-provider";

export default function LoginPage() {
  const { t } = useLanguage();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    getCurrentUser().then((user) => {
      if (user) window.location.replace("/");
    }).catch(() => undefined);
  }, []);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      await login(username, password);
      window.location.replace("/");
    } catch (reason) {
      setError(t(reason instanceof Error ? reason.message : "登录失败"));
      setLoading(false);
    }
  }

  return (
    <main className="login-shell">
      <section className="login-card">
        <div className="login-seal" aria-hidden="true">知</div>
        <p className="section-kicker">PRIVATE KNOWLEDGE ARCHIVE</p>
        <h1>Knowledge RAG</h1>
        <p className="login-intro">{t("使用管理员分配的账号登录。模型凭据和知识库配置仅在管理员后台维护。")}</p>
        <form className="login-form" onSubmit={onSubmit}>
          <label>
            <span>{t("用户名")}</span>
            <input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" required autoFocus />
          </label>
          <label>
            <span>{t("密码")}</span>
            <input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" required />
          </label>
          {error ? <p className="form-error" role="alert">{error}</p> : null}
          <button type="submit" disabled={loading}>{t(loading ? "正在登录…" : "登录知识库")}</button>
        </form>
      </section>
    </main>
  );
}
