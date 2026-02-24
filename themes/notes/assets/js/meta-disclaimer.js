(function () {
  // Stems to match — covers all case forms (Фейсбук, Фейсбуке, Фейсбука, etc.)
  var LATIN_WORDS = ['Instagram', 'Facebook', 'Threads', 'WhatsApp', 'Meta Platforms'];
  var CYRILLIC_STEMS = ['Инстаграм', 'Фейсбук', 'Тредс', 'Тредз', 'Ватсап'];

  var DISCLAIMER = 'Компания Meta Platforms\u00a0Inc. признана экстремистской организацией, ' +
    'её деятельность на\u00a0территории России запрещена.';

  // Latin words: standard \b works fine
  // Cyrillic stems: use lookahead for non-cyrillic char (or end of string)
  var latinPart = LATIN_WORDS.map(function (w) {
    return '\\b' + w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\b';
  }).join('|');

  var cyrillicPart = CYRILLIC_STEMS.map(function (s) {
    return s + '[а-яё]*';
  }).join('|');

  // Cyrillic boundary: not preceded/followed by a Cyrillic letter
  var pattern = new RegExp(
    '(' + latinPart + '|(?<![а-яёА-ЯЁ])(?:' + cyrillicPart + ')(?![а-яёА-ЯЁ]))',
    'gi'
  );

  function markTextNodes(root) {
    var found = false;
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null, false);
    var nodes = [];

    while (walker.nextNode()) {
      var node = walker.currentNode;
      var parent = node.parentElement;
      if (!parent) continue;
      var tag = parent.tagName;
      if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'CODE' || tag === 'PRE') continue;
      if (parent.classList && parent.classList.contains('meta-disclaimer')) continue;
      if (parent.classList && parent.classList.contains('meta-marked')) continue;

      if (pattern.test(node.nodeValue)) {
        nodes.push(node);
        found = true;
      }
      pattern.lastIndex = 0;
    }

    nodes.forEach(function (node) {
      var frag = document.createDocumentFragment();
      var text = node.nodeValue;
      var lastIndex = 0;
      var match;
      pattern.lastIndex = 0;

      while ((match = pattern.exec(text)) !== null) {
        if (match.index > lastIndex) {
          frag.appendChild(document.createTextNode(text.slice(lastIndex, match.index)));
        }
        var span = document.createElement('span');
        span.className = 'meta-marked';
        span.textContent = match[0];
        frag.appendChild(span);

        var asterisk = document.createElement('sup');
        asterisk.className = 'meta-asterisk';
        asterisk.textContent = '*';
        frag.appendChild(asterisk);

        lastIndex = pattern.lastIndex;
      }

      if (lastIndex < text.length) {
        frag.appendChild(document.createTextNode(text.slice(lastIndex)));
      }

      node.parentNode.replaceChild(frag, node);
    });

    return found;
  }

  function addDisclaimer(content) {
    var el = document.createElement('div');
    el.className = 'meta-disclaimer';
    el.innerHTML = '<span class="meta-disclaimer-asterisk">*</span> ' + DISCLAIMER;
    content.appendChild(el);
  }

  function init() {
    if (!document.body.classList.contains('post-template')) return;
    if (document.body.classList.contains('page-template')) return;

    var content = document.querySelector('.post-content');
    if (!content) return;

    var found = markTextNodes(content);
    if (found) {
      addDisclaimer(content);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
