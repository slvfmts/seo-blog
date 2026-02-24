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

    // Collect top-level block elements (p, h2, h3, ul, ol, blockquote, figure, pre)
    var blocks = [];
    for (var i = 0; i < content.children.length; i++) {
      var tag = content.children[i].tagName;
      if (/^(P|H2|H3|H4|UL|OL|BLOCKQUOTE|FIGURE|PRE)$/.test(tag)) {
        blocks.push(content.children[i]);
      }
    }

    if (blocks.length < 5) {
      // Short article — one banner at the end
      content.appendChild(createBanner('end'));
    } else {
      // Mid-banner after ~50% of blocks (but not before 3rd)
      var midIndex = Math.max(2, Math.floor(blocks.length / 2) - 1);
      var midTarget = blocks[midIndex];
      midTarget.parentNode.insertBefore(createBanner('mid'), midTarget.nextSibling);

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
