import { useState, type FormEvent } from "react";
import { Modal } from "@/components/modal";
import { useLanguage } from "@/components/language-provider";
import {
  createAdminUser,
  resetAdminUserPassword,
  setAdminUserKnowledgeBases,
  setAdminUserStatus,
  type AdminUser,
  type KnowledgeBase,
} from "@/lib/api";

export type DialogState =
  | { kind: "create" }
  | { kind: "access" | "password" | "status"; userId: string }
  | null;

export function UserDialogs({
  dialog,
  user,
  currentUserId,
  knowledgeBases,
  basesReady,
  onClose,
  onNotice,
  onCreated,
  onUpdated,
}: {
  dialog: NonNullable<DialogState>;
  user?: AdminUser;
  currentUserId: string;
  knowledgeBases: KnowledgeBase[];
  basesReady: boolean;
  onClose: () => void;
  onNotice: (text: string) => void;
  onCreated: (user: AdminUser) => void;
  onUpdated: (user: AdminUser) => void;
}) {
  const { t } = useLanguage();
  const [form, setForm] = useState({
    username: "",
    display_name: "",
    password: "",
    role: "user" as "admin" | "user",
    knowledge_base_ids: [] as string[],
  });
  const [access, setAccess] = useState<string[]>(
    user?.knowledge_base_ids ?? [],
  );
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const close = () => {
    if (!submitting) onClose();
  };
  const label =
    dialog.kind === "create"
      ? "创建用户"
      : dialog.kind === "access"
        ? "知识库权限"
        : dialog.kind === "password"
          ? "重置密码"
          : user?.active
            ? "禁用用户"
            : "启用用户";

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) return;
    setError("");
    setSubmitting(true);
    try {
      if (dialog.kind === "create") {
        if (!basesReady) throw new Error("知识库加载失败，授权操作暂不可用");
        onCreated(
          await createAdminUser({
            ...form,
            knowledge_base_ids:
              form.role === "admin" ? [] : form.knowledge_base_ids,
          }),
        );
        onNotice("用户已创建");
      } else {
        if (!user) throw new Error("用户不存在");
        if (dialog.kind === "access") {
          if (!basesReady) throw new Error("知识库加载失败，授权操作暂不可用");
          onUpdated(await setAdminUserKnowledgeBases(user.id, access));
          onNotice("知识库权限已更新");
        } else if (dialog.kind === "password") {
          await resetAdminUserPassword(user.id, password);
          setPassword("");
          if (user.id === currentUserId) {
            window.location.replace("/login");
            return;
          }
          onNotice("密码已重置，原有会话已失效");
        } else {
          if (user.id === currentUserId && user.active)
            throw new Error("不能禁用当前登录账户");
          onUpdated(await setAdminUserStatus(user.id, !user.active));
          onNotice(user.active ? "用户已禁用" : "用户已启用");
        }
      }
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "操作失败");
    } finally {
      setSubmitting(false);
    }
  }

  const choices = (selected: string[], change: (ids: string[]) => void) => (
    <fieldset
      className="create-access-fieldset"
      disabled={submitting || !basesReady}
    >
      <legend>{t("知识库权限")}</legend>
      {knowledgeBases.map((item) => (
        <label key={item.id}>
          <input
            type="checkbox"
            checked={selected.includes(item.id)}
            onChange={(event) =>
              change(
                event.target.checked
                  ? [...selected, item.id]
                  : selected.filter((id) => id !== item.id),
              )
            }
          />
          {item.name}
        </label>
      ))}
    </fieldset>
  );

  return (
    <Modal label={t(label)} onClose={close} className="user-management-modal">
      <form onSubmit={(event) => void submit(event)}>
        <h2>{t(label)}</h2>
        {user && dialog.kind !== "create" ? (
          <p>
            @{user.username} · {user.display_name}
          </p>
        ) : null}
        {dialog.kind === "create" ? (
          <>
            <label>
              <span>{t("用户名")}</span>
              <input
                required
                pattern="[A-Za-z0-9._-]{2,80}"
                value={form.username}
                onChange={(event) =>
                  setForm({ ...form, username: event.target.value })
                }
              />
            </label>
            <label>
              <span>{t("显示名称")}</span>
              <input
                required
                value={form.display_name}
                onChange={(event) =>
                  setForm({ ...form, display_name: event.target.value })
                }
              />
            </label>
            <label>
              <span>{t("初始密码")}</span>
              <input
                required
                minLength={10}
                type="password"
                autoComplete="new-password"
                value={form.password}
                onChange={(event) =>
                  setForm({ ...form, password: event.target.value })
                }
              />
            </label>
            <label>
              <span>{t("角色")}</span>
              <select
                value={form.role}
                onChange={(event) =>
                  setForm({
                    ...form,
                    role: event.target.value as "admin" | "user",
                  })
                }
              >
                <option value="user">{t("普通用户")}</option>
                <option value="admin">{t("管理员")}</option>
              </select>
            </label>
            {form.role === "user"
              ? choices(form.knowledge_base_ids, (ids) =>
                  setForm({ ...form, knowledge_base_ids: ids }),
                )
              : null}
          </>
        ) : null}
        {dialog.kind === "access" && user?.role === "user"
          ? choices(access, setAccess)
          : null}
        {dialog.kind === "password" ? (
          <label>
            <span>{t("新密码")}</span>
            <input
              required
              minLength={10}
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder={t("至少 10 个字符")}
            />
          </label>
        ) : null}
        {dialog.kind === "status" ? (
          <p>
            {t(
              user?.active
                ? "确定禁用此用户？禁用后该用户将无法登录。"
                : "确定启用此用户？",
            )}
          </p>
        ) : null}
        {error ? (
          <p className="form-error" role="alert">
            {t(error)}
          </p>
        ) : null}
        <footer className="user-dialog-actions">
          <button type="button" disabled={submitting} onClick={close}>
            {t("取消")}
          </button>
          <button
            type="submit"
            className="primary"
            disabled={
              submitting ||
              ((dialog.kind === "create" || dialog.kind === "access") &&
                !basesReady) ||
              (dialog.kind === "status" &&
                user?.id === currentUserId &&
                user.active)
            }
          >
            {t(submitting ? "处理中…" : label)}
          </button>
        </footer>
      </form>
    </Modal>
  );
}
