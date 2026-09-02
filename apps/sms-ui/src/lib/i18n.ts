/* i18n translator for SMS SPA.
   Mirrors prefs-extras.js logic: exact text-node swaps only.
   Synced from localStorage.ebLanguage; safe if dict is missing (guards with || {}).
*/

const I18N_DICT: Record<string, Record<string, string>> = {
  es: {},
  fr: {},
  pt: {},
};

// Populated by importing the dict at module load
let _originals = new WeakMap<Text, string>();
let _observer: MutationObserver | null = null;

export function loadI18nDict(dict: Record<string, any>) {
  // Transpose dict format { "English": {es,fr,pt} } → { es: {"English": "es trans"}, ...}
  for (const [enKey, langs] of Object.entries(dict)) {
    if (!langs || typeof langs !== "object") continue;
    if (langs.es) I18N_DICT.es[enKey] = langs.es;
    if (langs.fr) I18N_DICT.fr[enKey] = langs.fr;
    if (langs.pt) I18N_DICT.pt[enKey] = langs.pt;
  }
}

function translateNode(node: Text, langCode: string | null) {
  if (!langCode || langCode === "en-US" || langCode === "en-GB") return;
  const dict = I18N_DICT[langCode] || {};
  const original = _originals.get(node) || node.nodeValue || "";
  if (!_originals.has(node)) _originals.set(node, original);

  for (const [en, tr] of Object.entries(dict)) {
    if (original.includes(en)) {
      node.nodeValue = original.replace(en, tr);
      return;
    }
  }
  node.nodeValue = original;
}

export function translatePage(langCode: string | null = null) {
  if (!langCode) {
    const stored = localStorage.getItem("ebLanguage") || "English (US)";
    const codeMap: Record<string, string | null> = {
      "English (US)": null,
      "English (UK)": null,
      Spanish: "es",
      French: "fr",
      Portuguese: "pt",
    };
    langCode = codeMap[stored] || null;
  }

  const translateAll = () => {
    const walker = document.createTreeWalker(
      document.body,
      NodeFilter.SHOW_TEXT,
      {
        acceptNode: (n) => {
          const parent = n.parentElement;
          if (!parent) return NodeFilter.FILTER_REJECT;
          const tag = parent.tagName.toUpperCase();
          if (["SCRIPT", "STYLE", "NOSCRIPT", "TEXTAREA", "CODE"].includes(tag))
            return NodeFilter.FILTER_REJECT;
          if (parent.hasAttribute("data-no-i18n"))
            return NodeFilter.FILTER_REJECT;
          return NodeFilter.FILTER_ACCEPT;
        },
      }
    );

    let node;
    while ((node = walker.nextNode() as Text | null)) {
      translateNode(node, langCode);
    }
  };

  // Initial translation pass
  translateAll();

  // Watch for dynamic content and re-translate (keep observer alive at module level)
  if (!_observer) {
    _observer = new MutationObserver(
      (() => {
        let timeout: ReturnType<typeof setTimeout>;
        return () => {
          clearTimeout(timeout);
          timeout = setTimeout(() => translateAll(), 50);
        };
      })()
    );

    _observer.observe(document.body, {
      childList: true,
      subtree: true,
      characterData: false,
    });
  }
}
