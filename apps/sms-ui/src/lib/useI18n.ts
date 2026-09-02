/* React hook for i18n in the SMS SPA.
   Watches localStorage for language changes and re-translates the DOM.
   Runs on mount to catch React-rendered content.
*/
import { useEffect } from "react";
import { translatePage } from "./i18n";

export function useI18n() {
  useEffect(() => {
    // Run translator immediately (catches some static content)
    translatePage();

    // Watch for language changes in localStorage
    const handler = (e: StorageEvent) => {
      if (e.key === "ebLanguage") {
        setTimeout(() => translatePage(), 100);
      }
    };

    window.addEventListener("storage", handler);

    // Re-translate after React fully mounts and renders (longer delay for complex SPA)
    const timeout1 = setTimeout(() => translatePage(), 800);
    const timeout2 = setTimeout(() => translatePage(), 1600);

    return () => {
      window.removeEventListener("storage", handler);
      clearTimeout(timeout1);
      clearTimeout(timeout2);
    };
  }, []);
}
