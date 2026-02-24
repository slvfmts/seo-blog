(function() {
    // Russian prepositions/conjunctions/particles that shouldn't end a line
    var words = ['а','в','и','к','о','с','у','б','бы','ж','же','ли','ль','на','не','ни','но','об','от','до','за','из','по','то','ан','да','аж','вы','ещё','мы','её','их','им','ты','он','ей','ну','уж'];
    var pattern = new RegExp('(^|[\\s>]|&nbsp;)(' + words.join('|') + ') ', 'gi');

    function typograph(el) {
        // Only process text nodes, skip scripts/styles/code
        var children = el.childNodes;
        for (var i = 0; i < children.length; i++) {
            var node = children[i];
            if (node.nodeType === 3) { // text node
                var text = node.nodeValue;
                var replaced = text.replace(pattern, '$1$2\u00A0');
                if (replaced !== text) {
                    node.nodeValue = replaced;
                }
            } else if (node.nodeType === 1 && !/^(script|style|code|pre|textarea)$/i.test(node.tagName)) {
                typograph(node);
            }
        }
    }

    document.addEventListener('DOMContentLoaded', function() {
        var content = document.querySelector('.post-content');
        if (content) typograph(content);

        var excerpts = document.querySelectorAll('.post-card-excerpt');
        for (var i = 0; i < excerpts.length; i++) {
            typograph(excerpts[i]);
        }
    });
})();
