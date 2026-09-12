async function saveCaption() {
  await fetch('/state', {method:'POST', body:JSON.stringify({caption:document.querySelector('input').value})});
  document.querySelector('[role=status]').textContent='Saved';
}
function unusedCaption() { return 'never called'; }
document.querySelector('button').onclick = saveCaption;
