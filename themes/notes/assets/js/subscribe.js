(function () {
  var form = document.getElementById('subscribeForm');
  if (!form) return;

  form.addEventListener('submit', function (e) {
    e.preventDefault();

    var email = form.querySelector('input[name="email"]').value.trim();
    var consent = form.querySelector('input[name="consent"]').checked;
    var btn = form.querySelector('.grid-banner-btn');

    if (!email || !consent) return;

    btn.disabled = true;
    btn.textContent = '...';

    fetch('/api/subscribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email })
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.ok) {
          form.style.display = 'none';
          document.getElementById('subscribeSuccess').style.display = 'block';
        } else {
          btn.disabled = false;
          btn.textContent = 'Получить';
          alert('Ошибка: ' + (data.error || 'попробуйте позже'));
        }
      })
      .catch(function () {
        btn.disabled = false;
        btn.textContent = 'Получить';
        alert('Ошибка сети, попробуйте позже');
      });
  });
})();
