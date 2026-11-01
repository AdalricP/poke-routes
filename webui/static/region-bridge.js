/* Loaded into the route bench only when it was opened from the region page
   (/?region=...). Adds the route's `region` block (and endings) to /api/generate
   requests for that route, so the generator sees elevation, rivers, role, etc. */
(function () {
  "use strict";
  let extra = null;
  try { extra = JSON.parse(localStorage.getItem("pokeroutes-region-spec") || "null"); } catch (e) { extra = null; }
  if (!extra) return;
  const orig = window.fetch;
  window.fetch = function (url, opts) {
    try {
      if (String(url).indexOf("/api/generate") !== -1 && opts && typeof opts.body === "string") {
        const body = JSON.parse(opts.body);
        if (body.name === extra.name) {
          body.region = extra.region;
          if (extra.endings && extra.endings.length) body.endings = extra.endings;
          opts = Object.assign({}, opts, { body: JSON.stringify(body) });
        }
      }
    } catch (e) { /* leave the request as it was */ }
    return orig.call(this, url, opts);
  };
})();
