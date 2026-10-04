// Sorts any table with the "sortable" class when a header is clicked.
// Cells sort by their data-value attribute when present, so "1,234" sorts as a number.
(function () {
  function cellValue(row, index, numeric) {
    const cell = row.cells[index];
    const raw = cell.dataset.value ?? cell.textContent.trim();
    return numeric ? Number(raw) : raw.toLowerCase();
  }

  function sortBy(table, header) {
    const index = Array.from(header.parentNode.children).indexOf(header);
    const numeric = header.dataset.type !== "text";
    // A first click puts the biggest numbers first, but text from A to Z.
    const current = header.getAttribute("aria-sort");
    const descending = current ? current === "ascending" : numeric;
    const body = table.tBodies[0];
    const rows = Array.from(body.rows);

    rows.sort(function (a, b) {
      const left = cellValue(a, index, numeric);
      const right = cellValue(b, index, numeric);
      const order = left < right ? -1 : left > right ? 1 : 0;
      return descending ? -order : order;
    });

    table.querySelectorAll("th").forEach(function (th) { th.removeAttribute("aria-sort"); });
    header.setAttribute("aria-sort", descending ? "descending" : "ascending");
    rows.forEach(function (row) { body.appendChild(row); });
  }

  document.querySelectorAll("table.sortable").forEach(function (table) {
    table.querySelectorAll("th:not(.no-sort)").forEach(function (header) {
      header.tabIndex = 0;
      header.addEventListener("click", function () { sortBy(table, header); });
      header.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          sortBy(table, header);
        }
      });
    });
  });
})();
