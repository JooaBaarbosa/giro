// PDV: atalhos de teclado e foco. Sem dependências; carregado só em /caixa.
(function () {
  "use strict";

  function focusEntry() {
    var target = document.getElementById("weight") || document.getElementById("entry");
    if (target) {
      target.focus();
      if (typeof target.select === "function") target.select();
    }
  }

  function closeSubpanels() {
    var weightForm = document.querySelector("form[data-subpanel]");
    if (weightForm) weightForm.remove();
    document.querySelectorAll("details[data-subpanel][open]").forEach(function (d) {
      d.removeAttribute("open");
    });
    focusEntry();
  }

  // Destaque do último item: a linha nasce colorida e a classe de fade a apaga.
  // Com prefers-reduced-motion o CSS desliga a transição.
  function highlightLast() {
    var row = document.getElementById("pos-last");
    if (!row) return;
    window.requestAnimationFrame(function () {
      row.classList.add("pos-last-fade");
    });
  }

  document.addEventListener("keydown", function (event) {
    if (event.key === "F2") {
      event.preventDefault();
      focusEntry();
    } else if (event.key === "F4") {
      event.preventDefault();
      var form = document.getElementById("finish-form");
      if (form) form.requestSubmit();
    } else if (event.key === "Escape") {
      closeSubpanels();
    }
  });

  document.addEventListener("click", function (event) {
    var target = event.target;
    if (target && target.closest && target.closest("[data-close-subpanel]")) {
      closeSubpanels();
    }
  });

  document.body.addEventListener("htmx:afterSwap", function () {
    focusEntry();
    highlightLast();
  });

  highlightLast();
})();
