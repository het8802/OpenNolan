(function () {
  const LOCAL_HOSTS = /^(localhost|127\.0\.0\.1|\[::1\])$/;
  const EMAIL_RE = /[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi;
  const SENSITIVE_KEY_RE = /(email|company)/i;
  const DEFAULT_HOST = "https://us.i.posthog.com";
  const queue = [];
  const readyCallbacks = [];
  let ready = false;

  function withPageDefaults(props) {
    return Object.assign(
      {
        page_path: window.location.pathname,
        page_hash: window.location.hash || undefined,
      },
      props || {}
    );
  }

  function capture(name, props) {
    if (!name) return;
    const payload = withPageDefaults(props);
    if (ready && window.posthog && typeof window.posthog.capture === "function") {
      window.posthog.capture(name, payload);
      return;
    }
    queue.push([name, payload]);
  }

  function onReady(callback) {
    if (typeof callback !== "function") return;
    if (ready) callback(window.posthog);
    else readyCallbacks.push(callback);
  }

  window.openNolanTrack = capture;
  window.openNolanAnalytics = {
    capture,
    onReady,
    getFeatureFlag(key) {
      if (!ready || !window.posthog || typeof window.posthog.getFeatureFlag !== "function") return undefined;
      return window.posthog.getFeatureFlag(key);
    },
  };

  function redactEvent(event) {
    const props = event && event.properties;
    if (!props) return event;
    Object.keys(props).forEach((key) => {
      const value = props[key];
      if (SENSITIVE_KEY_RE.test(key)) {
        delete props[key];
        return;
      }
      if (typeof value === "string") {
        EMAIL_RE.lastIndex = 0;
        if (EMAIL_RE.test(value)) props[key] = value.replace(EMAIL_RE, "[redacted-email]");
      }
    });
    return event;
  }

  function flushQueue() {
    ready = true;
    while (queue.length) {
      const item = queue.shift();
      window.posthog.capture(item[0], item[1]);
    }
    while (readyCallbacks.length) {
      readyCallbacks.shift()(window.posthog);
    }
  }

  function isLocalPreview() {
    const forced = /(?:^|[?&])ph_debug=1(?:&|$)/.test(window.location.search);
    return !forced && (window.location.protocol === "file:" || LOCAL_HOSTS.test(window.location.hostname));
  }

  if (isLocalPreview()) return;

  fetch("/api/posthog-config", {
    headers: { Accept: "application/json" },
    credentials: "same-origin",
    cache: "no-store",
  })
    .then((res) => (res.ok ? res.json() : null))
    .then((config) => {
      if (!config || !config.enabled || !config.key) return;

      !function(t,e){var o,n,p,r;e.__SV||(window.posthog=e,e._i=[],e.init=function(i,s,a){function g(t,e){var o=e.split(".");2==o.length&&(t=t[o[0]],e=o[1]),t[e]=function(){t.push([e].concat(Array.prototype.slice.call(arguments,0)))}}(p=t.createElement("script")).type="text/javascript",p.crossOrigin="anonymous",p.async=!0,p.src=s.api_host.replace(".i.posthog.com","-assets.i.posthog.com")+"/static/array.js",(r=t.getElementsByTagName("script")[0]).parentNode.insertBefore(p,r);var u=e;for(void 0!==a?u=e[a]=[]:a="posthog",u.people=u.people||[],u.toString=function(t){var e="posthog";return"posthog"!==a&&(e+="."+a),t||(e+=" (stub)"),e},u.people.toString=function(){return u.toString(1)+".people (stub)"},o="init capture register register_once register_for_session unregister unregister_for_session getFeatureFlag getFeatureFlagPayload isFeatureEnabled reloadFeatureFlags updateEarlyAccessFeatureEnrollment getEarlyAccessFeatures on onFeatureFlags onSessionId getSurveys getActiveMatchingSurveys renderSurvey canRenderSurvey getNextSurveyStep identify setPersonProperties group resetGroups setPersonPropertiesForFlags resetPersonPropertiesForFlags setGroupPropertiesForFlags resetGroupPropertiesForFlags reset get_distinct_id getGroups get_session_id get_session_replay_url alias set_config startSessionRecording stopSessionRecording sessionRecordingStarted captureException loadToolbar get_property getSessionProperty createPersonProfile opt_in_capturing opt_out_capturing has_opted_in_capturing has_opted_out_capturing clear_opt_in_out_capturing debug".split(" "),n=0;n<o.length;n++)g(u,o[n]);e._i.push([i,s,a])},e.__SV=1)}(document,window.posthog||[]);

      window.posthog.init(config.key, {
        api_host: config.host || DEFAULT_HOST,
        ui_host: config.uiHost,
        defaults: "2026-05-30",
        capture_pageview: true,
        autocapture: {
          css_selector_ignorelist: [
            ".ph-no-autocapture",
            "[data-ph-no-autocapture]",
            "input[name='email']",
            "input[name='company']",
          ],
          element_attribute_ignorelist: ["value"],
        },
        property_denylist: ["email", "company"],
        person_profiles: "identified_only",
        before_send: redactEvent,
        loaded: flushQueue,
      });
    })
    .catch(() => {});
})();
