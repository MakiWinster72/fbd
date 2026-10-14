"use client";

import { FormEvent, useEffect, useState } from "react";
import { getCurrentUser, login } from "@/lib/api";
import { useLanguage } from "@/components/language-provider";

export default function LoginPage() {
  const { t } = useLanguage();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
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
    <main id="main-content" tabIndex={-1} className="login-shell">
      <section className="login-card">
        <div className="login-story">
          <div className="login-seal" aria-hidden="true">G</div>
          <p className="section-kicker">{t("私有知识库")}</p>
          <h1>{t("让每个答案，都有据可查。")}</h1>
          <p className="login-intro">{t("使用管理员分配的账号登录。模型凭据和知识库配置仅在管理员后台维护。")}</p>
          <div className="login-proof"><span>{t("原始文件")}</span><span>{t("页码引用")}</span><span>{t("访问授权")}</span></div>
        </div>
        <form className="login-form" onSubmit={onSubmit} aria-busy={loading}>
          <h2>{t("登录工作台")}</h2>
          <label>
            <span>{t("用户名")}</span>
            <input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" required autoFocus />
          </label>
          <div className="password-field">
            <label htmlFor="login-password">{t("密码")}</label>
            <div className="password-input">
              <input id="login-password" type={showPassword ? "text" : "password"} value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" required />
              <button type="button" aria-controls="login-password" aria-pressed={showPassword} onClick={() => setShowPassword((value) => !value)}>{t(showPassword ? "隐藏密码" : "显示密码")}</button>
            </div>
          </div>
          {error ? <p className="form-error" role="alert">{error}</p> : null}
          <button type="submit" disabled={loading}>{t(loading ? "正在登录…" : "登录知识库")}</button>
        </form>
      </section>
    </main>
  );
}
