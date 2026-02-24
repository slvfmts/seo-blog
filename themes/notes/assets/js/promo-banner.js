(function () {
  // --- Config ---
  var CONFIG = {
    title: 'Курс про фриланс для редакторов',
    text: 'Научитесь находить клиентов, вести проекты и зарабатывать на текстах — без бирж и демпинга.',
    btnText: 'Узнать подробнее',
    btnUrl: 'https://editors.one/unemployed?utm_source=notes&utm_medium=banner&utm_campaign=freelance_course',
    image: 'https://notes.editors.one/content/images/promo/freelance-course.webp'
  };

  function createBanner(position) {
    var banner = document.createElement('aside');
    banner.className = 'promo-banner promo-banner--' + position;
    banner.innerHTML =
      '<div class="promo-banner-inner">' +
        '<img class="promo-banner-image" src="' + CONFIG.image + '" alt="" loading="lazy" width="200" height="200">' +
        '<div class="promo-banner-content">' +
          '<div class="promo-banner-title">' + CONFIG.title + '</div>' +
          '<p class="promo-banner-text">' + CONFIG.text + '</p>' +
          '<a class="promo-banner-btn" href="' + CONFIG.btnUrl + '">' + CONFIG.btnText + '</a>' +
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
