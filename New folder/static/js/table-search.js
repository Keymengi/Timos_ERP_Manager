// Generic "as you type" filter for a record table.
// Usage: attachTableSearch('debtsSearchInput', '#debtsTable tbody tr[data-search]');
// Each <tr> needs data-search="<searchable text>" (customer name, status, etc).
function attachTableSearch(inputId, rowSelector) {
    const input = document.getElementById(inputId);
    if (!input) return;
    const rows = Array.from(document.querySelectorAll(rowSelector));

    input.addEventListener('input', function () {
        const q = input.value.trim().toLowerCase().replace(/\s+/g, '');
        rows.forEach(function (row) {
            const text = (row.dataset.search || '').toLowerCase().replace(/\s+/g, '');
            row.style.display = (!q || text.includes(q)) ? '' : 'none';
        });
    });
}
