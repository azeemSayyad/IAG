/* Shared by every script of the extension (content scripts + background via importScripts). */
/* eslint-disable no-unused-vars */
var IAG = (typeof IAG !== "undefined" && IAG) || {};

IAG.VERSION = "1.1.2";
IAG.PAGE_SOURCE = "iag-portal";   // messages FROM the portal page
IAG.EXT_SOURCE = "iag-hs-ext";    // messages TO the portal page

/* Portal origins the extension accepts jobs from (and reports back to). The
   manifest's host_permissions + content_scripts.matches must list the same
   hosts. Ports are ignored for localhost (local preview runs on several).
   scripts/build-hs-extension.sh strips the localhost entries from the zip
   agents install, so only the live portal can hand it a job. */
IAG.PORTAL_HOSTS = ["portal.iagplans.com", "127.0.0.1", "localhost"];

IAG.isPortalOrigin = function (origin) {
  try {
    var u = new URL(origin);
    if (IAG.PORTAL_HOSTS.indexOf(u.hostname) === -1) return false;
    // Live portal must be https; local preview is http.
    return u.protocol === "https:" || u.hostname === "127.0.0.1" || u.hostname === "localhost";
  } catch (e) {
    return false;
  }
};
