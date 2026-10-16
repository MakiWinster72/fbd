import type { AdminUser } from "@/lib/api";

export type UserAction = "access" | "password" | "status";

export function UserList({
  users,
  currentUserId,
  onAction,
  t,
}: {
  users: AdminUser[];
  currentUserId: string;
  onAction: (action: UserAction, userId: string) => void;
  t: (text: string) => string;
}) {
  const actions = (user: AdminUser) => (
    <div className="user-directory-actions">
      {user.role === "user" ? (
        <button type="button" onClick={() => onAction("access", user.id)}>
          {t("编辑权限")}
        </button>
      ) : null}
      <button type="button" onClick={() => onAction("password", user.id)}>
        {t("重置密码")}
      </button>
      <button
        type="button"
        disabled={user.id === currentUserId}
        title={
          user.id === currentUserId ? t("不能禁用当前登录账户") : undefined
        }
        onClick={() => onAction("status", user.id)}
      >
        {t(user.active ? "禁用用户" : "启用用户")}
      </button>
    </div>
  );
  const role = (user: AdminUser) =>
    t(user.role === "admin" ? "管理员" : "普通用户");
  const status = (user: AdminUser) => (
    <span className={`user-status ${user.active ? "active" : "inactive"}`}>
      {t(user.active ? "正常" : "已禁用")}
    </span>
  );
  const access = (user: AdminUser) =>
    t(user.role === "admin" ? "全部知识库" : "已授权知识库") +
    (user.role === "admin" ? "" : ` · ${user.knowledge_base_ids.length}`);
  return (
    <div className="user-directory">
      <table className="user-directory-table">
        <thead>
          <tr>
            <th>{t("用户名")}</th>
            <th>{t("显示名称")}</th>
            <th>{t("角色")}</th>
            <th>{t("状态")}</th>
            <th>{t("知识库权限")}</th>
            <th>{t("操作")}</th>
          </tr>
        </thead>
        <tbody>
          {users.map((user) => (
            <tr key={user.id}>
              <td>@{user.username}</td>
              <td>
                <strong>{user.display_name}</strong>
                {user.id === currentUserId ? (
                  <small className="user-directory-self">{t("当前账号")}</small>
                ) : null}
              </td>
              <td>{role(user)}</td>
              <td>{status(user)}</td>
              <td>{access(user)}</td>
              <td>{actions(user)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="user-directory-mobile">
        {users.map((user) => (
          <article className="user-directory-card" key={user.id}>
            <header>
              <div>
                <strong>{user.display_name}</strong>
                <small>
                  @{user.username}
                  {user.id === currentUserId ? ` · ${t("当前账号")}` : ""}
                </small>
              </div>
              {status(user)}
            </header>
            <p>
              {role(user)} · {access(user)}
            </p>
            {actions(user)}
          </article>
        ))}
      </div>
    </div>
  );
}
