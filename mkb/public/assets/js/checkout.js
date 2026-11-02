// Order / enrolment flow for packages, courses and services.
// 1) the buyer leaves details → 2) the order gets a reference → 3) pay (M-Pesa / Paystack if set up) and confirm in DMs.
import { esc, ICONS, money, openModal, submit, channelList, wireChannels, $, toast, links, currency } from './core.js';

const fmtKES = (n) => 'KES ' + Math.round(Number(n) || 0).toLocaleString('en-US');

function mpesaHTML(C, amount, ref) {
  const p = C.settings.payments || {};
  if (!p.mpesaType || !p.mpesaNumber) return '';
  const steps = {
    till: ['Go to M-Pesa → Lipa na M-Pesa → Buy Goods and Services', `Till number: <b class="mono">${esc(p.mpesaNumber)}</b>`],
    paybill: ['Go to M-Pesa → Lipa na M-Pesa → Pay Bill', `Business number: <b class="mono">${esc(p.mpesaNumber)}</b>`, `Account number: <b class="mono">${esc(ref)}</b>`],
    phone: ['Go to M-Pesa → Send Money', `Phone number: <b class="mono">${esc(p.mpesaNumber)}</b>`],
  }[p.mpesaType] || [];
  return `<div class="card" style="margin-top:18px">
    <span class="eyebrow eyebrow--plain">Pay with M-Pesa</span>
    <ol style="margin:14px 0 0;padding-left:20px;display:grid;gap:8px">
      ${steps.map((s) => `<li>${s}</li>`).join('')}
      ${amount ? `<li>Amount: <b class="mono">${fmtKES(amount)}</b></li>` : ''}
      ${p.mpesaName ? `<li>Confirm the name shows <b>${esc(p.mpesaName)}</b>, then enter your PIN</li>` : ''}
      <li>Send the M-Pesa confirmation message with your reference <b class="mono">${esc(ref)}</b></li>
    </ol></div>`;
}

let paystackLoading;
function loadPaystack() {
  if (window.PaystackPop) return Promise.resolve();
  if (!paystackLoading) {
    paystackLoading = new Promise((res, rej) => {
      const s = document.createElement('script'); s.src = 'https://js.paystack.co/v2/inline.js'; s.onload = res; s.onerror = () => rej(new Error('Could not load Paystack'));
      document.head.appendChild(s);
    });
  }
  return paystackLoading;
}

// item: { kind: 'package'|'course'|'service'|'waitlist', id, name, price, unit, group }
export function startCheckout(C, item) {
  const isWait = item.kind === 'waitlist';
  const paystack = (C.settings.payments?.paystackPublicKey || '').trim();
  const payable = !isWait && Number(item.price) > 0 && item.unit !== 'custom' && item.unit !== 'from';
  const kindLabel = { package: 'Package', course: 'Course enrolment', service: 'Service', waitlist: 'Waitlist' }[item.kind] || 'Order';
  const priceLine = Number(item.price) && item.unit !== 'custom' ? `${item.unit === 'from' ? 'from ' : ''}${money(item.price, C)}${item.unit && !['one-off', 'from', 'custom'].includes(item.unit) ? ' ' + item.unit : ''}` : 'Custom terms';

  const m = openModal(`
    <span class="eyebrow">${esc(kindLabel)}</span>
    <h2 style="margin:14px 0 6px">${esc(item.name)}</h2>
    <p class="muted" style="margin:0 0 24px">${isWait ? "Leave your details and you'll be first to know when enrolment opens." : esc(priceLine) + (currency() === 'USD' ? ` <span class="mono" style="font-size:.8rem">(${fmtKES(item.price)})</span>` : '')}</p>
    <form class="form-grid" novalidate>
      <div class="field"><label for="co-name">Full name</label><input class="input" id="co-name" name="name" required autocomplete="name" maxlength="80"></div>
      <div class="field"><label for="co-phone">WhatsApp / phone</label><input class="input" id="co-phone" name="phone" required autocomplete="tel" inputmode="tel" placeholder="07xx xxx xxx" maxlength="30"></div>
      <div class="field full"><label for="co-email">Email ${paystack && payable ? '' : '<span style="text-transform:none;letter-spacing:0;font-weight:600">(optional)</span>'}</label><input class="input" id="co-email" name="email" type="email" autocomplete="email" maxlength="120" ${paystack && payable ? 'required' : ''}></div>
      <div class="field full"><label for="co-notes">${item.kind === 'course' || isWait ? 'What do you want to achieve?' : 'Tell me about your project or goals'}</label><textarea class="textarea" id="co-notes" name="notes" maxlength="2000" placeholder="${item.kind === 'course' ? 'Your experience level, schedule, goals…' : 'What you need, deadlines, links to examples…'}"></textarea></div>
      <input class="hp" name="website" tabindex="-1" autocomplete="off" aria-hidden="true">
      <div class="full row"><button class="btn btn--primary btn--lg" type="submit">${isWait ? 'Join waitlist' : 'Continue'} ${ICONS.arrow}</button><span class="muted" style="font-size:.82rem">${esc(C.settings.responseTime || '')}</span></div>
    </form>`, { label: item.name });

  const form = $('form', m.el);
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = Object.fromEntries(new FormData(form));
    if (fd.website) return; // bot
    if (!fd.name?.trim() || !fd.phone?.trim()) { toast('Please add your name and WhatsApp/phone number.', 'err'); return; }
    if (form.email.required && !/^\S+@\S+\.\S+$/.test(fd.email || '')) { toast('Please add a valid email for card / M-Pesa checkout.', 'err'); return; }
    const btn = $('button[type=submit]', form); btn.disabled = true;
    let ref;
    try {
      ({ ref } = await submit(C, {
        type: isWait ? 'waitlist' : 'order', title: item.name, item: { kind: item.kind, id: item.id, name: item.name, price: item.price, unit: item.unit },
        name: fd.name.trim(), phone: fd.phone.trim(), email: (fd.email || '').trim(), message: (fd.notes || '').trim(),
      }));
    } catch (err) { btn.disabled = false; toast(err.message || 'Could not send. Try WhatsApp below.', 'err'); return; }

    const text = [
      `Hi Moses, ${isWait ? 'please add me to the waitlist for' : "I'd like to get started with"}: ${item.name}${payable ? ` (${fmtKES(item.price)}${item.unit && !['one-off'].includes(item.unit) ? ' ' + item.unit : ''})` : ''}.`,
      `Ref: ${ref}`,
      `Name: ${fd.name.trim()}`,
      `Phone: ${fd.phone.trim()}`,
      fd.email ? `Email: ${fd.email.trim()}` : '',
      fd.notes ? `\n${fd.notes.trim()}` : '',
    ].filter(Boolean).join('\n');

    const body = $('.modal__body', m.el);
    body.innerHTML = `
      <span class="eyebrow">${isWait ? "You're on the list" : 'Order received'}</span>
      <h2 style="margin:14px 0 8px">${esc(item.name)}</h2>
      <div class="copy-row" style="margin:16px 0"><span>Your reference: <b>${esc(ref)}</b></span><button class="btn btn--sm" data-copyref>Copy</button></div>
      ${!isWait ? `<p class="muted" style="margin:0">Next: ${payable ? 'pay below, then confirm' : 'confirm'} in my DMs so we can schedule your start.</p>` : `<p class="muted" style="margin:0">Send me a quick DM so I can reach you first when it opens.</p>`}
      ${payable && paystack ? `<button class="btn btn--primary btn--lg btn--block" style="margin-top:18px" data-paystack>Pay ${fmtKES(item.price)} with M-Pesa or card ${ICONS.arrow}</button>` : ''}
      ${payable ? mpesaHTML(C, item.price, ref) : ''}
      ${payable && !paystack && !C.settings.payments?.mpesaNumber ? `<div class="notice notice--green" style="margin-top:18px">${ICONS.info}<span>I'll confirm the details and send payment instructions on WhatsApp.</span></div>` : ''}
      <h3 class="h4" style="margin:26px 0 12px">Confirm in my DMs</h3>
      ${channelList(C, { text, subject: `${isWait ? 'Waitlist' : 'Order'} ${ref}: ${item.name}`, only: ['whatsapp', 'email', 'x', 'instagram', 'sms', 'call'] })}
      <div class="notice" style="margin-top:18px">${ICONS.shield}<span>${esc(C.settings.payments?.note || 'Only pay to the details shown on this website.')}</span></div>`;
    wireChannels(body, text);
    $('[data-copyref]', body).addEventListener('click', async () => { try { await navigator.clipboard.writeText(ref); toast('Reference copied'); } catch { toast(ref); } });
    $('[data-paystack]', body)?.addEventListener('click', async () => {
      try {
        await loadPaystack();
        const popup = new window.PaystackPop();
        popup.newTransaction({
          key: paystack, email: fd.email.trim(), amount: Math.round(Number(item.price) * 100), currency: 'KES', reference: ref,
          metadata: { custom_fields: [{ display_name: 'Item', variable_name: 'item', value: item.name }, { display_name: 'Phone', variable_name: 'phone', value: fd.phone.trim() }] },
          onSuccess: (tx) => { toast('Payment received — thank you! Ref ' + (tx.reference || ref)); window.open(links(C).whatsapp(`Hi Moses, I've paid for ${item.name}. Ref: ${tx.reference || ref}`), '_blank', 'noopener'); },
          onCancel: () => toast('Payment cancelled. You can pay with M-Pesa below or confirm on WhatsApp.', 'err'),
          onError: (er) => toast((er && er.message) || 'Payment could not start.', 'err'),
        });
      } catch (err) { toast(err.message, 'err'); }
    });
  });
  setTimeout(() => $('#co-name', m.el)?.focus(), 80);
  return m;
}
