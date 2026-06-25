/* OpenNolan — shared behavior for content/programmatic pages.
   Mirrors the homepage: nav scroll state, reveal-on-scroll, GitHub link wiring,
   year stamp, and the waitlist form (POST /api/waitlist) with PostHog + Vercel
   analytics. Self-contained, no deps. */
(function () {
  "use strict";
  var CONFIG = { githubUrl: "https://github.com/het8802/OpenNolan" };

  function track(name, props) { if (window.openNolanTrack) window.openNolanTrack(name, props || {}); }

  /* GitHub links — show only when configured (no broken links otherwise). */
  document.querySelectorAll("[data-github]").forEach(function (el) {
    if (CONFIG.githubUrl) { el.href = CONFIG.githubUrl; el.hidden = false; el.target = "_blank"; el.rel = "noopener"; }
    else { el.hidden = true; }
  });

  var yearEl = document.getElementById("year");
  if (yearEl) yearEl.textContent = new Date().getFullYear();

  /* Sticky nav shadow on scroll. */
  var nav = document.getElementById("nav");
  if (nav) {
    var onScroll = function () { nav.classList.toggle("scrolled", window.scrollY > 8); };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
  }

  /* Track in-page / GitHub CTA clicks (parity with the homepage). */
  document.addEventListener("click", function (e) {
    var target = e.target.closest("a,button");
    if (!target) return;
    var href = target.getAttribute("href") || "";
    if (href.charAt(0) === "#" || target.hasAttribute("data-github")) {
      track("cta_clicked", {
        label: target.textContent.trim().replace(/\s+/g, " ").slice(0, 80),
        href: href || undefined,
        section: target.closest("header") ? "nav" : target.closest("footer") ? "footer" : "body",
      });
    }
  });

  /* Reveal on scroll. */
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduceMotion) {
    document.querySelectorAll(".reveal").forEach(function (el) { el.classList.add("in"); });
  } else if ("IntersectionObserver" in window) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) { if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); } });
    }, { threshold: 0.12, rootMargin: "0px 0px -40px 0px" });
    document.querySelectorAll(".reveal").forEach(function (el) { io.observe(el); });
  } else {
    document.querySelectorAll(".reveal").forEach(function (el) { el.classList.add("in"); });
  }

  /* Waitlist form. */
  var EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  function setStatus(form, msg, type) {
    var s = form.querySelector(".form-status"); if (!s) return;
    s.textContent = msg || ""; s.className = "form-status" + (type ? " " + type : "");
  }
  function showSuccess(form) {
    var card = form.nextElementSibling && form.nextElementSibling.classList.contains("form-success-card") ? form.nextElementSibling : null;
    if (card) { form.style.display = "none"; card.classList.add("show"); }
    else { setStatus(form, "You're on the list 🎬 We'll email you at launch.", "success"); }
  }
  function placementOf(form) { return form.dataset.analyticsPlacement || "content_page"; }

  document.querySelectorAll(".waitlist-form").forEach(function (form) {
    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      var emailInput = form.querySelector('input[name="email"]');
      var honeypot = form.querySelector('input[name="company"]');
      var btn = form.querySelector('button[type="submit"]');
      var placement = placementOf(form);
      var email = (emailInput.value || "").trim().toLowerCase();
      if (!EMAIL_RE.test(email)) { track("waitlist_email_invalid", { placement: placement }); setStatus(form, "Please enter a valid email address.", "error"); emailInput.focus(); return; }
      var original = btn.textContent;
      btn.disabled = true; btn.textContent = "Joining…"; setStatus(form, "", "");
      track("waitlist_submit", { placement: placement });
      try {
        var res = await fetch("/api/waitlist", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email: email, company: honeypot ? honeypot.value : "", ref: location.pathname }) });
        if (res.ok) {
          var data = await res.json().catch(function () { return {}; });
          if (data.alreadyJoined) { track("waitlist_duplicate", { placement: placement }); setStatus(form, "You're already on the list — see you at launch! 🎬", "success"); }
          else {
            showSuccess(form);
            track("waitlist_signup", { placement: placement });
            if (window.va) { window.va("event", { name: "waitlist_signup", data: { placement: placement } }); }
          }
        } else {
          var err = await res.json().catch(function () { return {}; });
          track("waitlist_error", { placement: placement, status: res.status });
          setStatus(form, err.error || "Something went wrong. Please try again.", "error");
          btn.disabled = false; btn.textContent = original;
        }
      } catch (e2) {
        track("waitlist_network_error", { placement: placement });
        setStatus(form, "Network error — please try again in a moment.", "error");
        btn.disabled = false; btn.textContent = original;
      }
    });
  });
})();
