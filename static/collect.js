// Customer-side diagnostics collector.
//
// Collects ONLY: browser name/version, OS/platform, viewport size, timezone, UI language,
// and (only when the customer ticks the checkbox) a page URL the customer can see and edit.
//
// It deliberately never touches cookies, web storage, credentials, tokens or history.
(function () {
  "use strict";

  var form = document.getElementById("incident-form");
  if (!form) return;

  function setField(name, value) {
    var cell = form.querySelector('[data-field="' + name + '"]');
    var input = form.querySelector('input[name="' + name + '"]');
    if (cell) cell.textContent = value || "unknown";
    if (input) input.value = value || "";
  }

  // ---- browser + OS -------------------------------------------------------

  function browserFromUA(ua) {
    var rules = [
      [/Edg(?:e|A|iOS)?\/([\d.]+)/, "Edge"],
      [/OPR\/([\d.]+)/, "Opera"],
      [/SamsungBrowser\/([\d.]+)/, "Samsung Internet"],
      [/YaBrowser\/([\d.]+)/, "Yandex Browser"],
      [/Firefox\/([\d.]+)/, "Firefox"],
      [/(?:Chrome|CriOS)\/([\d.]+)/, "Chrome"],
      [/Version\/([\d.]+).*Safari\//, "Safari"]
    ];
    for (var i = 0; i < rules.length; i++) {
      var m = ua.match(rules[i][0]);
      if (m) return rules[i][1] + " " + m[1].split(".")[0];
    }
    return "Unknown";
  }

  function osFromUA(ua) {
    var m;
    if (/Windows NT 10\.0/.test(ua)) return "Windows 10/11";
    if (/Windows NT 6\.3/.test(ua)) return "Windows 8.1";
    if (/Windows NT 6\.1/.test(ua)) return "Windows 7";
    if ((m = ua.match(/Android ([\d.]+)/))) return "Android " + m[1];
    if ((m = ua.match(/iPhone OS ([\d_.]+)/))) return "iOS " + m[1].replace(/_/g, ".");
    if ((m = ua.match(/iPad; CPU OS ([\d_.]+)/))) return "iPadOS " + m[1].replace(/_/g, ".");
    if ((m = ua.match(/Mac OS X ([\d_.]+)/))) return "macOS " + m[1].replace(/_/g, ".");
    if (/CrOS/.test(ua)) return "ChromeOS";
    if (/Linux/.test(ua)) return "Linux";
    return "Unknown";
  }

  function windowsVersionFromPlatformVersion(pv) {
    // UA Client Hints: Windows platformVersion >= 13 means Windows 11.
    var major = parseInt(String(pv || "").split(".")[0], 10);
    if (isNaN(major)) return "Windows";
    if (major >= 13) return "Windows 11";
    if (major > 0) return "Windows 10";
    return "Windows 8.1 or older";
  }

  function detectBrowserAndOS() {
    var ua = navigator.userAgent || "";
    var fallback = { browser: browserFromUA(ua), os: osFromUA(ua) };
    var uad = navigator.userAgentData;
    if (!uad || !uad.getHighEntropyValues) return Promise.resolve(fallback);

    return uad.getHighEntropyValues(["platformVersion", "fullVersionList"]).then(function (hints) {
      var list = hints.fullVersionList || uad.brands || [];
      var picked = null;
      for (var i = 0; i < list.length; i++) {
        var b = list[i].brand;
        if (/Not.?A.?Brand/i.test(b) || b === "Chromium") continue;
        picked = list[i];
        break;
      }
      var browser = picked
        ? picked.brand.replace(/^(Google|Microsoft) /, "") + " " + String(picked.version).split(".")[0]
        : fallback.browser;
      var os = fallback.os;
      var platform = uad.platform || "";
      if (platform === "Windows") os = windowsVersionFromPlatformVersion(hints.platformVersion);
      else if (platform === "macOS" && hints.platformVersion) os = "macOS " + hints.platformVersion;
      else if (platform === "Android" && hints.platformVersion) os = "Android " + hints.platformVersion;
      else if (platform === "Chrome OS") os = "ChromeOS";
      else if (platform) os = platform;
      return { browser: browser, os: os };
    }, function () { return fallback; });
  }

  // ---- static facts ---------------------------------------------------------

  function viewport() {
    return window.innerWidth + "x" + window.innerHeight +
      (window.devicePixelRatio && window.devicePixelRatio !== 1 ? " @" + window.devicePixelRatio + "x" : "");
  }

  function timezone() {
    try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (e) { return ""; }
  }

  function language() {
    return navigator.language || (navigator.languages && navigator.languages[0]) || "";
  }

  setField("viewport", viewport());
  setField("timezone", timezone());
  setField("language", language());
  detectBrowserAndOS().then(function (r) {
    setField("browser", r.browser);
    setField("os", r.os);
  });
  window.addEventListener("resize", function () { setField("viewport", viewport()); });

  // ---- URL: explicit opt-in, always visible and editable ---------------------

  var shareUrl = document.getElementById("share_url");
  var urlRow = document.getElementById("url-row");
  var urlInput = document.getElementById("page_url");
  var params = new URLSearchParams(window.location.search);
  // Prefill only from sources the customer already sees: the ?from= hint set by support, or the
  // referring page (only present if the customer clicked the link from inside the product).
  var suggested = params.get("from") || document.referrer || "";
  if (urlInput && suggested && !urlInput.value) urlInput.value = suggested;
  if (shareUrl && urlRow) {
    shareUrl.addEventListener("change", function () {
      urlRow.hidden = !shareUrl.checked;
      if (!shareUrl.checked && urlInput) urlInput.value = suggested;
    });
  }

  // ---- screenshot preview ----------------------------------------------------

  var fileInput = form.querySelector('input[name="screenshot"]');
  var preview = document.getElementById("preview");
  var previewImg = document.getElementById("preview-img");
  if (fileInput && preview && previewImg) {
    fileInput.addEventListener("change", function () {
      var f = fileInput.files && fileInput.files[0];
      if (!f) { preview.hidden = true; return; }
      previewImg.src = URL.createObjectURL(f);
      preview.hidden = false;
    });
  }

  form.addEventListener("submit", function (ev) {
    var desc = form.querySelector('textarea[name="description"]');
    if (desc && !desc.value.trim()) {
      ev.preventDefault();
      desc.focus();
      desc.setCustomValidity("Please describe the problem");
      desc.reportValidity();
      desc.setCustomValidity("");
    }
  });
})();
