(function () {
  'use strict';

  // Only run on post pages (not pages, not index)
  if (!document.body.classList.contains('post-template')) return;

  var content = document.querySelector('.post-content');
  if (!content) return;

  var headings = content.querySelectorAll('h2');
  if (headings.length < 3) return;

  // Cyrillic transliteration map
  var cyr = {
    'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ё':'yo','ж':'zh',
    'з':'z','и':'i','й':'j','к':'k','л':'l','м':'m','н':'n','о':'o',
    'п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f','х':'kh','ц':'ts',
    'ч':'ch','ш':'sh','щ':'shch','ъ':'','ы':'y','ь':'','э':'e','ю':'yu','я':'ya'
  };

  function slugify(text) {
    return text.toLowerCase().split('').map(function (ch) {
      return cyr[ch] !== undefined ? cyr[ch] : ch;
    }).join('')
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-|-$/g, '');
  }

  // Add ids to headings and build items list
  var items = [];
  var usedIds = {};
  headings.forEach(function (h) {
    var slug = slugify(h.textContent.trim());
    if (!slug) slug = 'section';
    // Deduplicate
    if (usedIds[slug]) {
      usedIds[slug]++;
      slug = slug + '-' + usedIds[slug];
    } else {
      usedIds[slug] = 1;
    }
    h.id = slug;
    items.push({ id: slug, text: h.textContent.trim() });
  });

  // Build link list HTML
  function buildLinks(extraClass) {
    var html = '';
    items.forEach(function (item) {
      html += '<a href="#' + item.id + '" class="toc-link ' + (extraClass || '') + '">' + item.text + '</a>';
    });
    return html;
  }

  // === Desktop sidebar ===
  var sidebar = document.createElement('nav');
  sidebar.className = 'toc-sidebar';
  sidebar.setAttribute('aria-label', 'Оглавление');
  sidebar.innerHTML = '<div class="toc-nav">' + buildLinks() + '</div>';

  var postFull = document.querySelector('.post-full');
  postFull.appendChild(sidebar);

  // Position sidebar top at cover or content top
  var cover = document.querySelector('.post-cover');
  var anchor = cover || content;
  var anchorTop = anchor.offsetTop; // relative to .post-full
  sidebar.style.top = anchorTop + 'px';

  // === Mobile collapsible ===
  var mobile = document.createElement('details');
  mobile.className = 'toc-mobile';
  mobile.innerHTML = '<summary>Содержание</summary><nav class="toc-mobile-nav">' + buildLinks('toc-mobile-link') + '</nav>';
  content.parentNode.insertBefore(mobile, content);

  // Close mobile menu on link click
  mobile.querySelectorAll('.toc-mobile-link').forEach(function (link) {
    link.addEventListener('click', function () {
      mobile.removeAttribute('open');
    });
  });

  // === Intersection Observer for active state ===
  var links = sidebar.querySelectorAll('.toc-link');
  var activeIndex = 0;

  function setActive(index) {
    if (index === activeIndex && links[activeIndex] && links[activeIndex].classList.contains('is-active')) return;
    links.forEach(function (l) { l.classList.remove('is-active'); });
    if (links[index]) {
      links[index].classList.add('is-active');
      activeIndex = index;
    }
  }

  // Use rootMargin to trigger when heading crosses top ~30% of viewport
  var observer = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (!entry.isIntersecting) return;
      var idx = Array.prototype.indexOf.call(headings, entry.target);
      if (idx !== -1) setActive(idx);
    });
  }, {
    rootMargin: '0px 0px -70% 0px',
    threshold: 0
  });

  headings.forEach(function (h) { observer.observe(h); });
  setActive(0);

  // Smooth scroll
  document.documentElement.style.scrollBehavior = 'smooth';
})();
