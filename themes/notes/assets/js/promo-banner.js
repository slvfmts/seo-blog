(function () {
  // --- Config ---
  var CONFIG = {
    title: 'Курс про фриланс',
    text: 'Как искать клиентов, считать деньги, управлять проектами, договариваться с\u00a0сложными персонажами\u00a0— и\u00a0не\u00a0сходить с\u00a0ума.',
    btnText: 'Звучит интересно',
    baseUrl: 'https://editors.one/unemployed',
    image: 'https://notes.editors.one/content/images/promo/wolf.gif'
  };

  function buildUtmUrl() {
    // utm_campaign = first 3-5 slug words from current URL path
    var slug = location.pathname.replace(/^\/|\/$/g, '');
    var words = slug.split('-').slice(0, 5).join('-');
    var campaign = words || 'homepage';
    return CONFIG.baseUrl +
      '?utm_source=seo_blog&utm_medium=internal&utm_campaign=' +
      encodeURIComponent(campaign);
  }

  function createBanner(position) {
    var url = buildUtmUrl();
    var banner = document.createElement('aside');
    banner.className = 'promo-banner promo-banner--' + position;
    banner.innerHTML =
      '<div class="promo-banner-inner">' +
        '<img class="promo-banner-image" src="' + CONFIG.image + '" alt="" loading="lazy" width="200" height="200">' +
        '<div class="promo-banner-content">' +
          '<div class="promo-banner-title">' + CONFIG.title + '</div>' +
          '<p class="promo-banner-text">' + CONFIG.text + '</p>' +
          '<p class="promo-banner-promo">\u221220% по промокоду <span class="promo-banner-code">БЛОГ</span></p>' +
          '<a class="promo-banner-btn" href="' + url + '">' + CONFIG.btnText + '</a>' +
        '</div>' +
      '</div>';
    return banner;
  }

  function init() {
    // Only on post pages, not Ghost pages
    if (!document.body.classList.contains('post-template')) return;
    if (document.body.classList.contains('page-template')) return;

    var content = document.querySelector('.post-content');
    if (!content) return;

    // Collect all headings (h2, h3) as natural section boundaries
    var headings = content.querySelectorAll('h2, h3');

    if (headings.length < 3) {
      // Too few sections — one banner at the end only
      content.appendChild(createBanner('end'));
    } else {
      // Find heading closest to 45% of content height, but not before the 2nd.
      // Skip if previous sibling is also a heading (avoid inserting between h2→h3).
      var contentHeight = content.offsetHeight;
      var midHeading = null;
      for (var i = 1; i < headings.length; i++) {
        if (headings[i].offsetTop >= contentHeight * 0.45) {
          var prev = headings[i].previousElementSibling;
          if (prev && /^H[2-4]$/.test(prev.tagName)) continue;
          midHeading = headings[i];
          break;
        }
      }
      if (!midHeading) midHeading = headings[1];
      // Insert banner BEFORE the heading (after the end of previous section)
      midHeading.parentNode.insertBefore(createBanner('mid'), midHeading);

      // End banner
      content.appendChild(createBanner('end'));
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
