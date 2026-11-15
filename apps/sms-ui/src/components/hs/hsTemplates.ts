/* Ready-made campaign texts — "Final Texts — Double Filter Checked" (IAG SMS
 * library v2, Nov 2026: carrier spam filter + toughest-reader filter). Admins
 * pick one in the New campaign / Re-text drawers and can edit it before sending.
 * [Name] = the client's first name (the server fills it in). Keep the wording as
 * approved; to add or change one, edit this list and rebuild the SPA.
 * (The library's picture/MMS text is left out on purpose — campaigns send text only.) */

export type HsTemplate = {
  id: string;
  group: "ACA" | "Tax" | "Medicare";
  label: string;
  text: string;
  carrier: "LOW" | "MEDIUM" | "WATCH";
  consumer: "PASS" | "CAUTION";
  top?: boolean;
  reader: "Millennial" | "Senior";
  quote: string;
  note: string;
  /** Something that doesn't fit a HealthSherpa campaign as written. */
  warn?: string;
};

const CALL_WARN = "Promises a call — HealthSherpa campaigns send an authorization link and never call. Edit the ending (e.g. “Reply YES to review”) before using it here.";
const MEDICARE_WARN = "Medicare clients aren't in HealthSherpa (Marketplace/ACA only) — the lookup won't find them.";

export const HS_TEMPLATES: HsTemplate[] = [
  {
    id: "aca-prices", group: "ACA", label: "Prices going up", carrier: "LOW", consumer: "PASS", top: true, reader: "Millennial",
    text: "Insurance Alliance Group: Hey [Name], health insurance prices are going up for 2027. Is yours? Reply YES and we'll check, takes 2 min. STOP to opt out",
    quote: "“Wait... is MINE going up? 2 minutes is nothing.” Personal question + tiny time ask = reply.",
    note: "Have the agent text “Calling you now” before dialing. Millennials pick up when they expect it.",
  },
  {
    id: "aca-letter", group: "ACA", label: "Plan letter", carrier: "LOW", consumer: "PASS", top: true, reader: "Millennial",
    text: "Insurance Alliance Group: Hey [Name], got a letter about your 2027 health plan? Some changes may affect you. Reply YES to review. STOP to opt out",
    quote: "“Yeah, I got something in the mail and never opened it.” Ties to a real letter, so it feels legit.",
    note: "Rotate with “Prices going up”.",
  },
  {
    id: "aca-application", group: "ACA", label: "Application question", carrier: "LOW", consumer: "PASS", reader: "Millennial",
    text: "Insurance Alliance Group: Hey [Name], one question about your 2027 health insurance application. Reply YES to review now, takes 2 min. STOP to opt out",
    quote: "“One question, 2 minutes, now? Fine, let's get it done.” Task + same-day urgency.",
    note: "Test 100–200 first. If Verizon blocks it (40002/40003), switch “now” to “today” (152 chars). Send weekday mornings.",
  },
  {
    id: "aca-compare", group: "ACA", label: "Compare options", carrier: "LOW", consumer: "PASS", reader: "Millennial",
    text: "Insurance Alliance Group: Hey [Name], your 2027 health plan options are out. Want to compare yours side by side? Reply YES for a call. STOP to opt out",
    quote: "“Compare mine? Ok, that's actually useful.” Specific and about THEM.",
    note: "Good for clients who haven't renewed yet.", warn: CALL_WARN,
  },
  {
    id: "tax-affects", group: "Tax", label: "Health insurance + taxes", carrier: "MEDIUM", consumer: "PASS", reader: "Millennial",
    text: "Insurance Alliance Group: Hey [Name], your health insurance affects your taxes. Need help filing this year? Reply YES for a call. STOP to opt out",
    quote: "“Wait, it does?” Surprise fact + simple offer.",
    note: "Send to your existing clients only (Texas: insurance license doesn't cover tax offers to cold leads).", warn: CALL_WARN,
  },
  {
    id: "tax-price", group: "Tax", label: "Filing keeps price low", carrier: "MEDIUM", consumer: "PASS", reader: "Millennial",
    text: "Insurance Alliance Group: Hey [Name], filing taxes helps keep your health insurance price low. Need help filing? Reply YES for a call. STOP to opt out",
    quote: "Clear reason to care: my price.",
    note: "Best from January on, when tax forms arrive.", warn: CALL_WARN,
  },
  {
    id: "mc-envelope", group: "Medicare", label: "Envelope", carrier: "LOW", consumer: "PASS", reader: "Senior",
    text: "Insurance Alliance Group: Hi [Name], got that Medicare envelope? Plans may have changed. Want your doctors & meds checked? Reply YES. STOP to opt out",
    quote: "Tough senior: “My doctors and my pills, that's what I worry about.” Helpful, not salesy.",
    note: "Medicare enrollment ends Dec 7.", warn: MEDICARE_WARN,
  },
  {
    id: "mc-dental", group: "Medicare", label: "Dental, vision, hearing", carrier: "MEDIUM", consumer: "PASS", reader: "Senior",
    text: "Insurance Alliance Group: Hi [Name], some 2027 Medicare plans added dental, vision or hearing. Want to see if yours did? Reply YES. STOP to opt out",
    quote: "Tough senior: “I do need new glasses...” Real benefits they understand.",
    note: "Grocery card goes on the CALL, never in the text.", warn: MEDICARE_WARN,
  },
  {
    id: "mc-partb", group: "Medicare", label: "Part B", carrier: "MEDIUM", consumer: "PASS", reader: "Senior",
    text: "Insurance Alliance Group: Hi [Name], does your Medicare plan pay toward your Part B? That may change in 2027. Want us to check? Reply YES. STOP to opt out",
    quote: "Tough senior: “Mine does... wait, it's changing?” A check-up on THEIR plan, not a sales pitch.",
    note: "Tightest text: fits first names up to about 12 letters. Use first names only.", warn: MEDICARE_WARN,
  },
];

/** The text with [Name] / {name} filled in, plus the STOP line if missing — what the client sees. */
export function previewText(t: string, name = "Jane"): string {
  const s = t.replace(/\{\s*(first_?name|name)\s*\}|\[\s*(first_?name|name)\s*\]/gi, name).trim();
  return /stop/i.test(s) ? s : `${s} Reply STOP to opt out.`;
}

/** Characters as the library counts them ([Name] included as written). */
export function charCount(t: string): number {
  return previewText(t, "[Name]").length;
}
