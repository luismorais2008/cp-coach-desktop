(() => {
  function report(message, error = false) {
    if (typeof window.say === 'function') window.say(message, error);
  }

  function install(win) {
    let doc;
    try {
      doc = win.document;
      if (doc.__cpDownloads) return;
      doc.__cpDownloads = true;
    } catch (_) { return; }

    function intercept(anchor) {
      if (!window.pywebview?.api?.save_download || !/^(blob:|data:)/i.test(anchor.href)) return false;
      const name = anchor.download || 'download';
      (async () => {
        try {
          const response = await win.fetch(anchor.href);
          const blob = await response.blob();
          if (blob.size > 32 * 1024 * 1024) throw Error('Transferência demasiado grande (máximo 32 MiB).');
          const encoded = await new Promise((resolve, reject) => {
            const reader = new win.FileReader();
            reader.onload = () => resolve(reader.result.split(',')[1]);
            reader.onerror = () => reject(Error('Não foi possível ler a transferência.'));
            reader.readAsDataURL(blob);
          });
          const result = await window.pywebview.api.save_download(name, encoded);
          if (result.error) throw Error(result.error);
          report(result.cancelled ? 'Exportação cancelada.' : 'Guardado: ' + result.name);
        } catch (error) { report('Erro ao exportar: ' + error.message, true); }
      })();
      return true;
    }

    const click = win.HTMLAnchorElement.prototype.click;
    win.HTMLAnchorElement.prototype.click = function () {
      if (!intercept(this)) return click.call(this);
    };
    doc.addEventListener('click', event => {
      const anchor = event.target.closest?.('a');
      if (anchor && intercept(anchor)) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    }, true);
    function frame(element) {
      if (element.tagName !== 'IFRAME') return;
      install(element.contentWindow);
      element.addEventListener('load', () => install(element.contentWindow));
    }
    doc.querySelectorAll('iframe').forEach(frame);
    new win.MutationObserver(records => {
      for (const record of records) for (const node of record.addedNodes) {
        if (node.nodeType !== 1) continue;
        frame(node);
        node.querySelectorAll('iframe').forEach(frame);
      }
    }).observe(doc, {childList: true, subtree: true});
  }
  install(window);
})();
