import { useLanguage } from "@/components/language-provider";

type Props = { page: number; pages: number; total: number; onChange: (page: number) => void };

export function ImportPagination({ page, pages, total, onChange }: Props) {
  const { t } = useLanguage();
  if (!total) return null;
  return (
    <nav className="intake-pagination" aria-label={t("列表分页")}>
      <span role="status">{t("共")} {total} {t("条")} · {page} / {pages}</span>
      <div>
        <button type="button" disabled={page <= 1} onClick={() => onChange(page - 1)}>{t("上一页")}</button>
        <button type="button" disabled={page >= pages} onClick={() => onChange(page + 1)}>{t("下一页")}</button>
      </div>
    </nav>
  );
}
