async function save() { try { await fetch('/save'); } catch (e) { other . ui.error.textContent = 'Save failed'; } }
