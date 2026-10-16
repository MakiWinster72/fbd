import { AuthenticatedWorkspace } from "@/components/authenticated-workspace";

export const dynamic = "force-dynamic";

export default function HomePage() {
  const assistantIconUrl = process.env.ASSISTANT_ICON_URL?.trim();
  return <AuthenticatedWorkspace assistantIconUrl={assistantIconUrl} />;
}
