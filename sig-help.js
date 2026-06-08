/* sig-help.js
 * Subtle (?) icon + popover for inline UI explainers.
 *
 * Usage (markup):
 *   <sig-help key="compare.scorecard"></sig-help>
 *   <span data-help="pill.northstar.topline"></span>   (auto-upgraded)
 *
 * Usage (programmatic):
 *   SignalHelpUI.attach(node, 'compare.scorecard');
 *   SignalHelpUI.iconHtml('compare.scorecard')   // returns the HTML string
 *
 * Behavior:
 *   - Renders a small "?" badge next to whatever it sits beside.
 *   - Hover opens the popover (250ms delay so accidental hovers don't flash);
 *     focus or click also opens. Click-outside or Esc closes.
 *   - Content is fetched from SignalHelp.get(key). If the key is missing, the
 *     icon renders disabled with a console warning so missing copy is obvious.
 */
(function (global) {
  'use strict';

  var OPEN_DELAY_MS = 200;
  var CLOSE_DELAY_MS = 120;
  var _popoverEl = null;
  var _activeTrigger = null;
  var _openTimer = null;
  var _closeTimer = null;

  function _ensurePopover() {
    if (_popoverEl) return _popoverEl;
    var el = document.createElement('div');
    el.className = 'sig-help-pop';
    el.setAttribute('role', 'tooltip');
    el.hidden = true;
    el.innerHTML =
      '<div class="sig-help-pop-arrow"></div>' +
      '<div class="sig-help-pop-body">' +
        '<div class="sig-help-pop-title"></div>' +
        '<div class="sig-help-pop-oneliner"></div>' +
        '<div class="sig-help-pop-howto-label">How to read it</div>' +
        '<div class="sig-help-pop-howto"></div>' +
      '</div>';
    document.body.appendChild(el);
    _popoverEl = el;
    // Keep popover open while hovering it.
    el.addEventListener('mouseenter', function () { _cancelClose(); });
    el.addEventListener('mouseleave', function () { _scheduleClose(); });
    return el;
  }

  function _populate(content) {
    var pop = _ensurePopover();
    var titleEl = pop.querySelector('.sig-help-pop-title');
    var oneEl = pop.querySelector('.sig-help-pop-oneliner');
    var howLabelEl = pop.querySelector('.sig-help-pop-howto-label');
    var howEl = pop.querySelector('.sig-help-pop-howto');
    titleEl.textContent = content.title || '';
    oneEl.textContent = content.oneLiner || '';
    if (content.howToRead) {
      howLabelEl.style.display = '';
      howEl.style.display = '';
      howEl.textContent = content.howToRead;
    } else {
      howLabelEl.style.display = 'none';
      howEl.style.display = 'none';
    }
  }

  function _position(trigger) {
    var pop = _ensurePopover();
    pop.hidden = false;
    pop.style.visibility = 'hidden';
    pop.style.left = '0px';
    pop.style.top = '0px';
    pop.classList.remove('sig-help-pop-above');

    var rect = trigger.getBoundingClientRect();
    var popRect = pop.getBoundingClientRect();
    var margin = 8;
    var spaceBelow = window.innerHeight - rect.bottom;
    var placeAbove = (spaceBelow < popRect.height + margin + 12) && (rect.top > popRect.height + margin);

    var top = placeAbove ? (rect.top - popRect.height - margin) : (rect.bottom + margin);
    var left = rect.left + rect.width / 2 - popRect.width / 2;
    left = Math.max(8, Math.min(left, window.innerWidth - popRect.width - 8));

    pop.style.top = (top + window.scrollY) + 'px';
    pop.style.left = (left + window.scrollX) + 'px';
    pop.classList.toggle('sig-help-pop-above', placeAbove);

    // Position the arrow so it points at the trigger.
    var arrow = pop.querySelector('.sig-help-pop-arrow');
    if (arrow) {
      var arrowLeft = (rect.left + rect.width / 2) - left - 6;
      arrow.style.left = Math.max(8, Math.min(arrowLeft, popRect.width - 16)) + 'px';
    }

    pop.style.visibility = '';
  }

  function _open(trigger) {
    var key = trigger.getAttribute('data-help-key');
    if (!key) return;
    var content = global.SignalHelp && global.SignalHelp.get(key);
    if (!content) {
      console.warn('[sig-help] missing help content for key:', key);
      return;
    }
    _activeTrigger = trigger;
    _populate(content);
    _position(trigger);
    trigger.setAttribute('aria-expanded', 'true');
  }

  function _close() {
    _cancelOpen();
    _cancelClose();
    if (_popoverEl) _popoverEl.hidden = true;
    if (_activeTrigger) _activeTrigger.setAttribute('aria-expanded', 'false');
    _activeTrigger = null;
  }

  function _scheduleOpen(trigger) {
    _cancelOpen();
    _cancelClose();
    _openTimer = setTimeout(function () { _open(trigger); }, OPEN_DELAY_MS);
  }
  function _scheduleClose() {
    _cancelClose();
    _closeTimer = setTimeout(_close, CLOSE_DELAY_MS);
  }
  function _cancelOpen() { if (_openTimer) { clearTimeout(_openTimer); _openTimer = null; } }
  function _cancelClose() { if (_closeTimer) { clearTimeout(_closeTimer); _closeTimer = null; } }

  function iconHtml(key, extraClass) {
    var hasContent = global.SignalHelp && global.SignalHelp.has(key);
    var cls = 'sig-help-icon' + (hasContent ? '' : ' sig-help-icon-missing') + (extraClass ? ' ' + extraClass : '');
    var aria = 'Help: ' + key;
    // Use a button so it's keyboard-focusable; type="button" to avoid form submits.
    return (
      '<button type="button" class="' + cls + '" data-help-key="' + _attr(key) +
      '" aria-label="' + _attr(aria) + '" aria-expanded="false" tabindex="0">' +
      '<span aria-hidden="true">?</span>' +
      '</button>'
    );
  }

  function _attr(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  // Delegate event handling on document — works for static and dynamic markup.
  function _wireDelegation() {
    if (document.body.dataset.sigHelpWired === '1') return;
    document.body.dataset.sigHelpWired = '1';

    document.body.addEventListener('mouseover', function (e) {
      var t = e.target && e.target.closest && e.target.closest('.sig-help-icon');
      if (!t) return;
      if (_activeTrigger === t) { _cancelClose(); return; }
      _scheduleOpen(t);
    });

    document.body.addEventListener('mouseout', function (e) {
      var t = e.target && e.target.closest && e.target.closest('.sig-help-icon');
      if (!t) return;
      _cancelOpen();
      _scheduleClose();
    });

    document.body.addEventListener('focusin', function (e) {
      var t = e.target && e.target.closest && e.target.closest('.sig-help-icon');
      if (!t) return;
      _open(t);
    });
    document.body.addEventListener('focusout', function (e) {
      var t = e.target && e.target.closest && e.target.closest('.sig-help-icon');
      if (!t) return;
      _scheduleClose();
    });

    document.body.addEventListener('click', function (e) {
      var t = e.target && e.target.closest && e.target.closest('.sig-help-icon');
      if (t) {
        e.preventDefault();
        e.stopPropagation();
        if (_activeTrigger === t && _popoverEl && !_popoverEl.hidden) _close();
        else _open(t);
        return;
      }
      // Click outside closes popover.
      if (_popoverEl && !_popoverEl.hidden && !_popoverEl.contains(e.target)) _close();
    });

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && _popoverEl && !_popoverEl.hidden) _close();
    });

    window.addEventListener('scroll', function () {
      if (_popoverEl && !_popoverEl.hidden && _activeTrigger) _position(_activeTrigger);
    }, true);
    window.addEventListener('resize', function () {
      if (_popoverEl && !_popoverEl.hidden && _activeTrigger) _position(_activeTrigger);
    });
  }

  // Auto-upgrade [data-help] elements: each one gets a (?) icon inserted after it.
  function _autoUpgrade(root) {
    var scope = root || document;
    var nodes = scope.querySelectorAll('[data-help]:not([data-help-upgraded])');
    nodes.forEach(function (node) {
      var key = node.getAttribute('data-help');
      if (!key) return;
      node.setAttribute('data-help-upgraded', '1');
      node.insertAdjacentHTML('beforeend', ' ' + iconHtml(key, 'sig-help-icon-inline'));
    });
  }

  // Public API
  global.SignalHelpUI = {
    iconHtml: iconHtml,
    open: _open,
    close: _close,
    attach: function (node, key) {
      if (!node || !key) return;
      node.insertAdjacentHTML('beforeend', ' ' + iconHtml(key));
    },
    autoUpgrade: _autoUpgrade
  };

  function _boot() {
    _wireDelegation();
    _autoUpgrade();
    // Watch the DOM for late-rendered surfaces (Compare/Drilldown etc.) so
    // [data-help] markup added by renderers gets upgraded automatically.
    if (typeof MutationObserver !== 'undefined') {
      var mo = new MutationObserver(function (muts) {
        for (var i = 0; i < muts.length; i++) {
          var mut = muts[i];
          if (mut.addedNodes && mut.addedNodes.length) { _autoUpgrade(); return; }
        }
      });
      mo.observe(document.body, { childList: true, subtree: true });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _boot);
  } else {
    _boot();
  }

})(window);
