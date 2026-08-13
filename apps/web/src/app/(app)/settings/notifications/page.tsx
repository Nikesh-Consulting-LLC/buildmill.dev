import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { requireOrg } from "../require-org";
import {
  NotificationsSettings,
  type EndpointRow,
} from "../notifications-settings";

export default async function NotificationsSettingsPage() {
  const { supabase, orgId } = await requireOrg();

  const { data: endpoints } = await supabase
    .from("notification_endpoints")
    .select(
      "id, name, url_host, format, last_delivery_at, last_delivery_ok, last_delivery_error"
    )
    .eq("org_id", orgId)
    .order("name", { ascending: true });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Notification endpoints</CardTitle>
        <CardDescription>
          Webhooks that hear about deployment outcomes — failures and
          rollbacks by default. Deliberately generic: later event families
          reuse these endpoints.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <NotificationsSettings
          orgId={orgId}
          endpoints={(endpoints ?? []) as EndpointRow[]}
        />
      </CardContent>
    </Card>
  );
}
