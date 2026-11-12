// Unio website: the price calculator (same rule as the service: 5% off per user after the first, up to 30%) and sign-up.
(function () {
  var LOCAL = /localhost$/.test(location.hostname);
  var API = LOCAL ? 'http://my.unio.localhost:8569' : 'https://my.unio.et';
  var DOMAIN = LOCAL ? 'unio.localhost:8569' : 'unio.et';
  var PRICE = { ETB: 999, UGX: 21900, KES: 775, USD: 5.99 }, STEP = 5, CAP = 30;
  function fmt(v, cur) {
    var d = cur === 'USD' ? 2 : 0;
    return (cur === 'USD' ? '$' : '') + Number(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }) + (cur === 'ETB' ? ' birr' : cur === 'USD' ? '' : ' ' + cur);
  }
  function quote(n, cur) {
    var disc = Math.min(STEP * (n - 1), CAP), each = PRICE[cur] * (1 - disc / 100);
    each = cur === 'USD' ? Math.round(each * 100) / 100 : Math.round(each);
    return { each: each, total: cur === 'USD' ? Math.round(each * n * 100) / 100 : each * n, disc: disc };
  }
  var signin = document.getElementById('signin');
  if (signin) signin.addEventListener('click', function (e) {
    e.preventDefault();
    location.href = 'signin.html';
  });
  // Ethiopia sees birr, everyone else USD (other currencies later): the device's time zone or Amharic language tells
  var tz = '';
  try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (e) { /* old browser */ }
  var ET = tz === 'Africa/Addis_Ababa' || /^am\b/i.test(navigator.language || '');
  var cur = ET ? 'ETB' : 'USD';
  var std = document.getElementById('std-price'), free = document.getElementById('free-price');
  if (ET && std) { std.innerHTML = '999 <small>birr per user a month</small>'; }
  if (ET && free) { free.innerHTML = '0 <small>birr, forever</small>'; }
  // calculator
  var range = document.getElementById('users');
  if (range) {
    var draw = function () {
      var n = +range.value, q = quote(n, cur);
      document.getElementById('users-out').textContent = n;
      document.getElementById('total').textContent = fmt(q.total, cur);
      document.getElementById('each').textContent = n + (n === 1 ? ' user' : ' users') + ' × ' + fmt(q.each, cur) + (q.disc ? ' (' + q.disc + '% off each)' : '');
    };
    range.addEventListener('input', draw);
    draw();
  }
  // the new address gets its secure certificate within seconds: open the door once it answers
  function ready(url) {
    var done = document.getElementById('done'), link = done.querySelector('a');
    link.href = url + '/web/login';
    done.querySelector('.where').textContent = url.replace(/^https?:\/\//, '');
    done.hidden = false;
    link.classList.add('wait'); link.textContent = 'Securing your address…';
    var tries = 0;
    (function knock() {
      fetch(url + '/web/login', { mode: 'no-cors', cache: 'no-store' }).then(function () {
        link.classList.remove('wait'); link.textContent = 'Open my system';
      }).catch(function () {
        if (++tries < 45) { setTimeout(knock, 2000); } else { link.classList.remove('wait'); link.textContent = 'Open my system'; }
      });
    })();
  }
  function post(path, data) {
    return fetch(API + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) })
      .then(function (r) { return r.json(); })
      .then(function (r) { if (!r.ok) { throw new Error(r.message || 'Something went wrong.'); } return r; });
  }
  function say(el, text) { el.textContent = text === 'Failed to fetch' ? 'The connection dropped. Please check your internet and try again.' : text; el.hidden = false; }

  // subscribe page (from the trial banner: ?c=<web address>&u=<users>)
  var sform = document.getElementById('sub-form');
  if (sform) {
    var q = new URLSearchParams(location.search), sub = (q.get('c') || '').toLowerCase().replace(/[^a-z0-9-]/g, '');
    var users = document.getElementById('sub-users');
    users.value = Math.min(Math.max(parseInt(q.get('u') || '3', 10) || 3, 1), 50);
    sform.elements.subdomain.value = sub;
    document.getElementById('sub-company').textContent = sub ? sub + '.' + DOMAIN : '';
    var period = q.get('p') === 'yearly' ? 'yearly' : 'monthly', priceTimer = null;
    var periodBtns = document.querySelectorAll('.period button');
    periodBtns.forEach(function (b) {
      b.classList.toggle('on', b.dataset.p === period);
      b.addEventListener('click', function () {
        period = b.dataset.p;
        periodBtns.forEach(function (x) { x.classList.toggle('on', x === b); });
        paint();
      });
    });
    var paint = function () {
      var n = +users.value, qq = quote(n, cur), yearly = period === 'yearly';
      // the quick figure here; the exact one (referral discounts, free months) comes from the service just after
      document.getElementById('sub-total').textContent = fmt(yearly ? qq.total * 10 : qq.total, cur);
      document.getElementById('sub-per').textContent = yearly ? 'a year' : 'a month';
      document.getElementById('sub-n').textContent = n + (n === 1 ? ' user' : ' users');
      document.getElementById('sub-each').textContent = n + ' × ' + fmt(qq.each, cur) + ' a month' + (qq.disc ? ' (' + qq.disc + '% off each)' : '');
      var sv = document.getElementById('sub-saving');
      sv.hidden = !yearly;
      if (yearly) { sv.textContent = 'You save ' + fmt(qq.total * 2, cur) + ': 12 months for the price of 10.'; }
      clearTimeout(priceTimer);
      if (!sub) { return; }
      priceTimer = setTimeout(function () {
        fetch(API + '/unio/pay?c=' + encodeURIComponent(sub) + '&u=' + n + '&p=' + period).then(function (x) { return x.json(); }).then(function (p) {
          if (!p.ok) { return; }
          var c = p.currency || cur;
          document.getElementById('sub-total').textContent = fmt(p.total, c);
          if (p.notes && p.notes.length) {
            sv.hidden = false;
            sv.textContent = (p.saving ? 'You save ' + fmt(p.saving, c) + '. ' : '') + p.notes.join(' · ');
          }
        }).catch(function () { /* the quick figure stays */ });
      }, 300);
    };
    users.addEventListener('input', paint);
    paint();
    // back from telebirr's checkout: wait for telebirr to confirm the payment
    // the payment page may drop part of the return address: the browser also remembers the payment it started
    var order = q.get('o');
    if (!order) { try { order = sessionStorage.getItem('unio_pay_order'); } catch (e) { order = null; } }
    if (order) { try { sessionStorage.removeItem('unio_pay_order'); } catch (e) { /* private mode */ } }
    if (order) {
      document.getElementById('sub-box').hidden = true;
      var title = document.getElementById('paid-title'), text = document.getElementById('paid-text'), tries = 0;
      title.textContent = 'Checking your payment…'; text.textContent = 'The payment is being confirmed; this takes a few seconds.';
      document.getElementById('paid-done').hidden = false;
      (function ask() {
        fetch(API + '/unio/pay_status?o=' + encodeURIComponent(order)).then(function (x) { return x.json(); }).then(function (s) {
          if (s.state === 'confirmed') {
            title.textContent = 'You are subscribed'; text.textContent = 'Your payment is confirmed: ' + s.company + ' is now subscribed. Thank you!';
          } else if (s.state === 'waiting' && ++tries < 20) { setTimeout(ask, 3000); }
          else { title.textContent = 'Payment not confirmed yet'; text.textContent = 'If you completed the payment, it will be confirmed within minutes and we will email you. If you cancelled, you can pay again from your system.'; }
        }).catch(function () { if (++tries < 20) setTimeout(ask, 3000); });
      })();
      return;
    }
    sform.addEventListener('submit', function (e) {
      e.preventDefault();
      var msg = document.getElementById('msg'), btn = sform.querySelector('button[type=submit]');
      msg.hidden = true;
      if (!sub) { say(msg, 'Open this page from the "Subscribe" button in your system.'); return; }
      btn.disabled = true; btn.textContent = 'Sending…';
      post('/unio/subscribe', { subdomain: sub, email: sform.elements.email.value, users: users.value, note: sform.elements.note.value, period: period }).then(function (r) {
        document.getElementById('sub-box').hidden = true;
        if (r.free_month) {
          document.getElementById('sub-box').hidden = true;
          document.getElementById('paid-title').textContent = 'You are subscribed';
          document.getElementById('paid-text').textContent = 'This month is free: you earned it by referring other businesses. ' + r.company + ' is now subscribed.';
          document.getElementById('paid-done').hidden = false;
          return;
        }
        var money = Number(r.total).toLocaleString('en-US') + ' ' + r.currency;
        document.getElementById('sub-done-text').textContent = 'The invoice for ' + r.company + ' (' + r.users + ' users, ' + money +
          (period === 'yearly' ? ' for 12 months' : ' a month') + ') is on its way by email. Pay it now and your subscription starts as soon as the payment is confirmed.';
        document.getElementById('sub-done').hidden = false;
        fetch(API + '/unio/pay?c=' + encodeURIComponent(sub) + '&u=' + encodeURIComponent(users.value) + '&p=' + period).then(function (x) { return x.json(); }).then(function (p) {
          if (p.bank) { document.getElementById('pay-bank').textContent = 'Or by bank transfer: ' + p.bank; }
          if (p.notes && p.notes.length) {
            var n = document.getElementById('sub-done-text');
            n.textContent += ' ' + p.notes.join('; ') + ': ' + Number(p.total).toLocaleString('en-US') + ' instead of ' + Number(p.full).toLocaleString('en-US') + '.';
          }
          if (!p.qr && !p.chapa && !p.checkout) { return; }
          if (p.qr) {
            document.getElementById('pay-qr').src = p.qr;
            document.getElementById('pay-to').textContent = 'To ' + p.merchant.name + ' · telebirr ' + p.merchant.code;
            document.getElementById('pay-code').textContent = p.merchant.code;
            document.getElementById('pay-amount').textContent = Number(p.total).toLocaleString('en-US') + ' birr';
          } else {
            ['pay-qr', 'pay-to', 'pay-or'].forEach(function (id) { document.getElementById(id).hidden = true; });
            document.querySelector('.pay-steps').hidden = true; document.getElementById('paid-form').hidden = true;
          }
          document.getElementById('pay-box').hidden = false;
          var go = function (btn, provider, label) {
            btn.disabled = true; btn.classList.add('wait'); btn.textContent = 'Opening the payment page…';
            post('/unio/checkout', { subdomain: sub, email: sform.elements.email.value, users: users.value, provider: provider, period: period })
              .then(function (c) { try { sessionStorage.setItem('unio_pay_order', c.order); } catch (e) { /* private mode */ } location.href = c.url; })
              .catch(function (err) { say(document.getElementById('paid-msg'), err.message); btn.disabled = false; btn.classList.remove('wait'); btn.textContent = label; });
          };
          if (p.chapa) {
            var ch = document.getElementById('pay-chapa'), chLabel = 'Pay ' + Number(p.total).toLocaleString('en-US') + ' ' + (p.currency === 'ETB' ? 'birr' : p.currency) + ': CBE Birr, telebirr, M-Pesa or card';
            ch.textContent = chLabel; ch.hidden = false; if (p.qr) { document.getElementById('pay-or').hidden = false; }
            ch.addEventListener('click', function () { go(ch, 'chapa', chLabel); });
          }
          if (p.checkout) {
            var online = document.getElementById('pay-online');
            online.textContent = 'Pay ' + Number(p.total).toLocaleString('en-US') + ' birr with telebirr';
            online.hidden = false; if (p.qr) { document.getElementById('pay-or').hidden = false; }
            online.addEventListener('click', function () {
              online.disabled = true; online.classList.add('wait'); online.textContent = 'Opening telebirr…';
              post('/unio/checkout', { subdomain: sub, email: sform.elements.email.value, users: users.value })
                .then(function (c) { try { sessionStorage.setItem('unio_pay_order', c.order); } catch (e) { /* private mode */ } location.href = c.url; })
                .catch(function (err) { say(document.getElementById('paid-msg'), err.message); online.disabled = false; online.classList.remove('wait'); online.textContent = 'Pay with telebirr'; });
            });
          }
        });
        var paid = document.getElementById('paid-form');
        paid.addEventListener('submit', function (ev) {
          ev.preventDefault();
          var m = document.getElementById('paid-msg'), b = paid.querySelector('button');
          m.hidden = true; b.disabled = true; b.textContent = 'Checking…';
          post('/unio/paid', { subdomain: sub, email: sform.elements.email.value, users: users.value, reference: paid.elements.reference.value, period: period })
            .then(function (x) {
              document.getElementById('sub-done').hidden = true;
              document.getElementById('paid-title').textContent = x.state === 'confirmed' ? 'You are subscribed' : 'Thank you';
              document.getElementById('paid-text').textContent = x.state === 'confirmed'
                ? 'telebirr confirmed your payment: ' + x.company + ' is now subscribed. The receipt is on its way by email.'
                : 'We received your transaction number and will confirm the payment shortly (usually within the hour, during working hours).';
              document.getElementById('paid-done').hidden = false;
            }).catch(function (err) { say(m, err.message); b.disabled = false; b.textContent = 'I have paid'; });
        });
      }).catch(function (err) { say(msg, err.message); btn.disabled = false; btn.textContent = 'Request my subscription'; });
    });
    return;
  }

  // confirmation page: the link from the email
  var vform = document.getElementById('verify-form');
  if (vform) {
    var token = new URLSearchParams(location.search).get('t') || '';
    var what = document.getElementById('verify-what');
    fetch(API + '/unio/verify?t=' + encodeURIComponent(token)).then(function (r) { return r.json(); }).then(function (r) {
      if (!r.ok) { throw new Error(r.message); }
      if (r.done && r.url) { document.getElementById('verify-box').hidden = true; ready(r.url); return; }
      what.innerHTML = '';
      what.append('Your email is confirmed. Choose a password to create ', Object.assign(document.createElement('b'), { textContent: r.company }),
        ' at ', Object.assign(document.createElement('b'), { textContent: r.subdomain + '.' + DOMAIN }), '.');
      vform.elements.email.value = r.email;
      vform.hidden = false;
      vform.elements.password.focus();
    }).catch(function (err) {
      what.hidden = true;
      say(document.getElementById('link-msg'), err.message);
      document.getElementById('again').hidden = false;
    });
    vform.addEventListener('submit', function (e) {
      e.preventDefault();
      var msg = document.getElementById('msg'), btn = vform.querySelector('button[type=submit]');
      msg.hidden = true;
      if (vform.elements.password.value.length < 8) { say(msg, 'Choose a password of at least 8 characters.'); return; }
      if (vform.elements.password.value !== vform.elements.password2.value) { say(msg, 'The two passwords differ.'); return; }
      btn.disabled = true; btn.textContent = 'Creating your system…';
      post('/unio/verify', { t: token, password: vform.elements.password.value }).then(function (r) {
        document.getElementById('verify-box').hidden = true;
        ready(r.url);
      }).catch(function (err) {
        // the reply can be lost on a bad connection even though the system was made: ask the link before asking to retry
        fetch(API + '/unio/verify?t=' + encodeURIComponent(token)).then(function (r) { return r.json(); }).then(function (r) {
          if (r.ok && r.done && r.url) { document.getElementById('verify-box').hidden = true; ready(r.url); } else { throw err; }
        }).catch(function () { say(msg, err.message); btn.disabled = false; btn.textContent = 'Create my system'; });
      });
    });
    return;
  }

  // sign-up form
  var form = document.getElementById('signup-form');
  if (!form) return;
  document.querySelectorAll('.domain').forEach(function (s) { s.textContent = '.' + DOMAIN; });
  var addr = form.elements.subdomain, company = form.elements.company, hint = document.getElementById('addr-hint'), touched = false, timer;
  function slug(t) { return (t || '').toLowerCase().replace(/[^a-z0-9]+/g, '').slice(0, 30); }
  function check() {
    clearTimeout(timer);
    timer = setTimeout(function () {
      var v = addr.value;
      if (!v) { hint.textContent = ''; return; }
      fetch(API + '/unio/check?name=' + encodeURIComponent(v)).then(function (r) { return r.json(); }).then(function (r) {
        hint.textContent = r.ok ? '✓ ' + r.subdomain + '.' + DOMAIN + ' is free' : r.message;
        hint.className = 'hint ' + (r.ok ? 'good' : 'bad');
      }).catch(function () { hint.textContent = ''; });
    }, 350);
  }
  company.addEventListener('input', function () { if (!touched) { addr.value = slug(company.value); check(); } });
  addr.addEventListener('input', function () { touched = true; addr.value = slug(addr.value); check(); });
  var params = new URLSearchParams(location.search);
  // invited by another business: show what the invitation gives
  if (params.get('ref')) {
    fetch(API + '/unio/ref?code=' + encodeURIComponent(params.get('ref'))).then(function (x) { return x.json(); }).then(function (r) {
      if (!r.valid) { return; }
      var box = document.getElementById('ref-offer');
      box.textContent = r.by + ' invited you: ' + r.pct + '% off your first ' + r.months + ' paid months.';
      box.hidden = false;
    }).catch(function () { /* the sign-up works without it */ });
  }
  // the form's signed token, and the robot check when it is switched on
  var formToken = '', robot = null;
  fetch(API + '/unio/form').then(function (r) { return r.json(); }).then(function (r) {
    formToken = r.form_token || '';
    if (r.turnstile_site_key) {
      window.onUnioTurnstile = function () {
        robot = window.turnstile.render('#robot-check', {
          sitekey: r.turnstile_site_key, appearance: 'interaction-only',
          'error-callback': function () { say(document.getElementById('msg'), 'The robot check could not load. Reload the page; if it keeps happening, try another browser or network.'); }
        });
      };
      var sc = document.createElement('script');
      sc.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?onload=onUnioTurnstile&render=explicit';
      sc.async = true;
      document.head.appendChild(sc);
    }
  }).catch(function () { /* the form still works; the server tells if something is missing */ });
  var sent = null;
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var msg = document.getElementById('msg'), btn = form.querySelector('button[type=submit]');
    msg.hidden = true;
    var data = { form_token: formToken, plan: params.get('plan') || 'standard', referrer_code: params.get('ref') || '' };
    ['company', 'subdomain', 'country', 'industry', 'users', 'name', 'email', 'phone', 'website'].forEach(function (k) { data[k] = form.elements[k].value; });
    if (robot !== null && window.turnstile) { data.turnstile = window.turnstile.getResponse(robot) || ''; }
    btn.disabled = true; btn.textContent = 'Sending…';
    post('/unio/signup', data).then(function (r) {
      if (r.token) { location.href = 'verify.html?t=' + encodeURIComponent(r.token); return; }
      sent = { email: r.email, subdomain: r.subdomain };
      document.getElementById('signup-box').hidden = true;
      var box = document.getElementById('sent');
      box.querySelector('.to').textContent = r.email;
      box.hidden = false;
    }).catch(function (err) {
      say(msg, err.message); btn.disabled = false; btn.textContent = 'Continue';
      if (robot !== null && window.turnstile) { window.turnstile.reset(robot); }
    });
  });
  var again = document.getElementById('resend');
  if (again) again.addEventListener('click', function () {
    var m = document.getElementById('resend-msg');
    again.disabled = true;
    post('/unio/resend', sent).then(function () { say(m, 'Sent again. It can take a minute.'); })
      .catch(function (err) { say(m, err.message); })
      .then(function () { setTimeout(function () { again.disabled = false; }, 60000); });
  });
})();
