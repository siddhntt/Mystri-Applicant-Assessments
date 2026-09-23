const currency = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' });
const money = n => currency.format(n);
const text = (tag, value, className = '') => {
  const node = document.createElement(tag);
  node.textContent = value;
  node.className = className;
  return node;
};

async function refresh() {
  const status = document.querySelector('#status').value;
  const responses = await Promise.all([fetch('/api/overview'), fetch(`/api/invoices?status=${status}`)]);
  if (responses.some(r => !r.ok)) throw new Error('Could not refresh the register.');
  const [data, rows] = await Promise.all(responses.map(r => r.json()));
  document.querySelector('#invoice-count').textContent = data.summary.invoice_count;
  document.querySelector('#open-count').textContent = data.summary.open_count;
  document.querySelector('#outstanding').textContent = money(data.summary.outstanding);
  document.querySelector('#overdue-count').textContent = data.summary.overdue_count ?? 0;
  const body = document.querySelector('#invoices');
  body.replaceChildren();
  rows.forEach(r => {
    const row = document.createElement('tr');
    if (r.overdue) row.classList.add('overdue');
    [r.customer_name, r.invoice_number, r.due_date].forEach(v => row.append(text('td', v)));
    [r.amount, r.paid, r.balance].forEach(v => row.append(text('td', money(v), 'number')));
    const statusCell = text('td', r.overdue ? `${r.status} · overdue` : r.status);
    row.append(statusCell);
    body.append(row);
  });
  const unmatched = document.querySelector('#unmatched');
  unmatched.replaceChildren(...data.unmatched_payments.map(p => text('li', `${p.payment_id} · ${p.customer_id} / ${p.invoice_number} · ${money(p.amount)}`)));
  if (!data.unmatched_payments.length) unmatched.append(text('li', 'No unmatched payments.'));
  document.querySelector('#page-error').textContent = '';
}

async function submitImport(form) {
  const feedback = form.querySelector('.feedback');
  const button = form.querySelector('button');
  button.disabled = true;
  feedback.textContent = 'Importing…';
  try {
    const csv = await form.querySelector('input').files[0].text();
    const response = await fetch(`/api/import?kind=${form.dataset.kind}`, {
      method: 'POST', headers: { 'Content-Type': 'text/csv' }, body: csv
    });
    const result = await response.json();
    if (!response.ok) {
      feedback.textContent = `Import failed: ${result.error}`;
    } else {
      let msg = `Imported: ${result.imported}, Skipped: ${result.skipped}, Rejected: ${result.rejected}`;
      if (result.errors && result.errors.length) {
        msg += '\n' + result.errors.map(e => `Line ${e.line}: ${e.reason}`).join('\n');
      }
      feedback.textContent = msg;
      await refresh();
    }
  } catch (error) {
    feedback.textContent = `Import failed: ${error.message}`;
  } finally {
    button.disabled = false;
  }
}

document.querySelector('#status').addEventListener('change', () => refresh().catch(e => { document.querySelector('#page-error').textContent = e.message; }));
document.querySelectorAll('form[data-kind]').forEach(form => form.addEventListener('submit', e => { e.preventDefault(); submitImport(form); }));
refresh().catch(e => { document.querySelector('#page-error').textContent = e.message; });
