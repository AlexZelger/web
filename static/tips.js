document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("tipForm");
  const resultBox = document.getElementById("resultBox");
  if (!form || !resultBox) return;

  form.addEventListener("submit", async (e) => {
    e.preventDefault();

    // Read values
    const totalEl = document.getElementById("total");
    const tipEl = document.getElementById("tip_percentage");
    const peopleEl = document.getElementById("num_people");

    const total = parseFloat(totalEl?.value || "0");
    const tip_percentage = parseFloat(tipEl?.value || "0");
    const num_people = parseInt(peopleEl?.value || "1", 10);

    // Basic guardrails
    if (isNaN(total) || isNaN(tip_percentage) || isNaN(num_people) || num_people < 1) {
      resultBox.textContent = "Please enter valid numbers (people must be ≥ 1).";
      resultBox.classList.remove("hidden");
      return;
    }

    // Endpoint provided by HTML via data-endpoint
    const endpoint = form.dataset.endpoint || "/calculate_tips";

    try {
      const res = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ total, tip_percentage, num_people })
      });

      if (!res.ok) {
        let msg = `Request failed: ${res.status} ${res.statusText}`;
        try {
          const err = await res.json();
          if (err?.error) msg = err.error;
        } catch {}
        resultBox.textContent = "Error: " + msg;
        resultBox.classList.remove("hidden");
        return;
      }

      const data = await res.json(); // expects { tip_amount, total_with_tip, per_person }
      const tip = Number(data.tip_amount ?? 0).toFixed(2);
      const totalWith = Number(data.total_with_tip ?? 0).toFixed(2);
      const each = Number(data.per_person ?? 0).toFixed(2);

      resultBox.textContent = `Tip: $${tip} | Total: $${totalWith} | Each: $${each}`;
      resultBox.classList.remove("hidden");
    } catch (err) {
      resultBox.textContent = "Network error. Please try again.";
      resultBox.classList.remove("hidden");
      console.error(err);
    }
  });
});