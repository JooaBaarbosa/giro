// PDV: atalhos de teclado, foco, abas de pagamento e teclado numérico. Sem dependências.
(function () {
  "use strict";

  var AMOUNT_LABELS = {
    cash: "Valor recebido",
    pix: "Valor no Pix",
    credit: "Valor no crédito",
    debit: "Valor no débito",
  };

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

  // Valores em reais trafegam como centavos inteiros para não errar arredondamento.
  function toCents(text) {
    var clean = String(text || "").replace(/\s/g, "").replace(/\./g, "").replace(",", ".");
    var value = Number(clean);
    return isFinite(value) ? Math.round(value * 100) : NaN;
  }

  function brl(cents) {
    var reais = Math.floor(cents / 100).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ".");
    return "R$ " + reais + "," + String(cents % 100).padStart(2, "0");
  }

  function updatePayment() {
    var amount = document.getElementById("pay-amount");
    if (!amount) return;
    var method = document.getElementById("pay-method").value;
    var remaining = Math.round(Number(amount.dataset.remaining) * 100);
    var cents = toCents(amount.value);
    var change = document.getElementById("pay-change");
    var submit = document.getElementById("pay-submit");
    var covers = !isNaN(cents) && cents >= remaining;
    change.textContent = method === "cash" && covers && cents > remaining
      ? "Troco " + brl(cents - remaining)
      : "";
    submit.textContent = covers || isNaN(cents) ? "Concluir venda" : "Lançar parcial";
  }

  function selectMethod(button) {
    var form = document.getElementById("pay-form");
    if (!form || !button) return;
    form.querySelectorAll("[data-pay-method]").forEach(function (b) {
      b.setAttribute("aria-pressed", b === button ? "true" : "false");
    });
    var method = button.dataset.payMethod;
    document.getElementById("pay-method").value = method;
    document.getElementById("pay-amount-label").textContent =
      AMOUNT_LABELS[method] || "Valor";
    var amount = document.getElementById("pay-amount");
    amount.dataset.fresh = "1";
    amount.focus();
    amount.select();
    updatePayment();
  }

  // O primeiro toque no teclado troca o valor sugerido; os seguintes acrescentam.
  function pressKey(key) {
    var amount = document.getElementById("pay-amount");
    if (!amount) return;
    var digits = amount.dataset.fresh === "0" ? amount.value.replace(/\D/g, "") : "";
    amount.dataset.fresh = "0";
    digits = key === "back" ? digits.slice(0, -1) : (digits + key).replace(/^0+(?=\d)/, "");
    var cents = Number(digits || "0");
    amount.value = brl(cents).replace("R$ ", "");
    updatePayment();
    // O foco volta ao valor: assim o Enter lança o pagamento em vez de repetir a tecla.
    amount.focus();
  }

  document.addEventListener("keydown", function (event) {
    if (event.key === "F2") {
      event.preventDefault();
      focusEntry();
    } else if (event.key === "F4") {
      event.preventDefault();
      if (document.getElementById("weight")) return;
      selectMethod(document.querySelector('[data-pay-method="cash"]'));
    } else if (event.key === "Escape") {
      closeSubpanels();
    }
  });

  document.addEventListener("click", function (event) {
    var target = event.target && event.target.closest ? event.target : null;
    if (!target) return;
    if (target.closest("[data-close-subpanel]")) {
      closeSubpanels();
      return;
    }
    var methodButton = target.closest("[data-pay-method]");
    if (methodButton) {
      selectMethod(methodButton);
      return;
    }
    var keyButton = target.closest("[data-key]");
    if (keyButton) pressKey(keyButton.dataset.key);
  });

  document.addEventListener("input", function (event) {
    if (event.target && event.target.id === "pay-amount") {
      event.target.dataset.fresh = "0";
      updatePayment();
    }
  });

  // Destaque do último item: o cartão nasce colorido e a classe de fade o suaviza.
  // Com prefers-reduced-motion o CSS desliga a transição.
  function highlightLast() {
    var card = document.getElementById("pos-last");
    if (!card) return;
    window.requestAnimationFrame(function () {
      window.requestAnimationFrame(function () {
        card.classList.add("pos-last-fade");
      });
    });
  }

  function afterRender() {
    var amount = document.getElementById("pay-amount");
    if (amount) amount.dataset.fresh = "1";
    updatePayment();
    focusEntry();
    highlightLast();
  }

  document.body.addEventListener("htmx:afterSwap", afterRender);
  afterRender();
})();
