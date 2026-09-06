/* Lead detail block for leads uploaded STRAIGHT to the pool (source=CSV_DIRECT).

   These leads never texted us — there is no conversation. The agent reads the
   number off the card and dials it on their own phone, so everything the
   uploaded row carried (carrier, plan, premium, dates, …) is laid out here in
   file order. Used compact in the offer popup and in full once accepted. */

export type DetailField = [string, string];

export type PoolLead = {
  id: string;
  phone_number: string;
  customer_name: string | null;
  address?: string | null;
  source?: string;
  details?: DetailField[];
};

export const isPoolLead = (l: { source?: string } | null | undefined) => !!l && l.source === "CSV_DIRECT";

/** Small pill that says how the lead got here. */
export function SourceBadge({ source, className = "" }: { source?: string; className?: string }) {
  const csv = source === "CSV_DIRECT";
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold ${
        csv ? "bg-accent/12 text-accent" : "bg-success/12 text-success"
      } ${className}`}
      title={csv ? "Uploaded to the pool — no text was sent. Call by phone." : "Replied to our text"}
    >
      <span aria-hidden>{csv ? "📋" : "💬"}</span>
      {csv ? "From list · call" : "Replied"}
    </span>
  );
}

/** Label/value grid of the uploaded columns. `limit` trims for the popup. */
export function DetailGrid({ fields, limit, dense }: { fields?: DetailField[]; limit?: number; dense?: boolean }) {
  const all = (fields || []).filter(([, v]) => v);
  if (all.length === 0) return null;
  const shown = limit ? all.slice(0, limit) : all;
  const more = all.length - shown.length;
  return (
    <div>
      <dl className={`grid gap-x-4 ${dense ? "grid-cols-2 gap-y-1.5" : "grid-cols-1 gap-y-2 sm:grid-cols-2"}`}>
        {shown.map(([label, value]) => (
          <div key={label} className="min-w-0 rounded-lg border border-hairline-soft bg-white/60 px-2.5 py-1.5">
            <dt className="truncate text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{label}</dt>
            <dd className={`break-words font-medium text-ink ${dense ? "text-[13px]" : "text-sm"}`}>{value}</dd>
          </div>
        ))}
      </dl>
      {more > 0 && (
        <div className="mt-1.5 text-[11px] text-ink-faint">+{more} more after you accept</div>
      )}
    </div>
  );
}
