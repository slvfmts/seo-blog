(function () {
  // Words to mark with an asterisk (case-insensitive)
  // Covers: Instagram, Инстаграм, Facebook, Фейсбук, Threads, Тредс, Тредз, WhatsApp, Ватсап
  var KEYWORDS = [
    'Instagram', 'Инстаграм', 'Инстаграме', 'Инстаграма', 'Инстаграму',
    'Facebook', 'Фейсбук', 'Фейсбуке', 'Фейсбука', 'Фейсбуку',
    'Threads', 'Тредс', 'Тредз',
    'WhatsApp', 'Ватсап', 'Ватсапе', 'Ватсапа',
    'Meta'
  ];

  var DISCLAIMER = 'Компания Meta Platforms\u00a0Inc. признана экстремистской организацией, ' +
    'её деятельность на\u00a0территории России запрещена.';

  // Build regex: match whole words only, case-insensitive
  var pattern = new RegExp(
    '\\b(' + KEYWORDS.join('|') + ')\\b',
    'gi'
  );

  function markTextNodes(root) {
    var found = false;
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null, false);
    var nodes = [];

    // Collect text nodes first (can't modify DOM while walking)
    while (walker.nextNode()) {
      var node = walker.currentNode;
      // Skip nodes inside scripts, styles, links, and already-marked spans
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

    // Replace text nodes with marked versions
    nodes.forEach(function (node) {
      var frag = document.createDocumentFragment();
      var text = node.nodeValue;
      var lastIndex = 0;
      var match;
      pattern.lastIndex = 0;

      while ((match = pattern.exec(text)) !== null) {
        // Text before match
        if (match.index > lastIndex) {
          frag.appendChild(document.createTextNode(text.slice(lastIndex, match.index)));
        }
        // The keyword + asterisk
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

      // Remaining text
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
    // Only on post pages
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
