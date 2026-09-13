// Support UI helpers: copy-to-clipboard for freshly created links.
(function () {
  "use strict";

  document.addEventListener("click", function (ev) {
    var target = ev.target;
    if (!(target instanceof HTMLElement)) return;

    if (target.hasAttribute("data-select-on-click") && target instanceof HTMLInputElement) {
      target.select();
      return;
    }

    var selector = target.getAttribute("data-copy");
    if (!selector) return;
    var input = document.querySelector(selector);
    if (!input) return;
    var text = input.value;
    var done = function () {
      var original = target.textContent;
      target.textContent = "Copied";
      setTimeout(function () { target.textContent = original; }, 1500);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () { input.select(); });
    } else {
      input.select();
      done();
    }
  });
})();
