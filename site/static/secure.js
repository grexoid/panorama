/* Runs on the pages of a password-protected site, after they have been unlocked by gate.js.
   Images are published only in encrypted form (data-enc-src="....png.enc"); this decrypts them with the
   session key and hands them to the page as blob: URLs. Nothing here runs on an unprotected site. */
(() => {
  'use strict';

  const images = [...document.querySelectorAll('img[data-enc-src]')];
  let stored = null;
  try { stored = sessionStorage.getItem('gallery-key'); } catch (e) { /* no storage, no images */ }
  if (!images.length || !stored || !window.crypto || !crypto.subtle) return;

  const TYPES = {
    png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', webp: 'image/webp',
    gif: 'image/gif', svg: 'image/svg+xml', avif: 'image/avif',
  };
  const key = crypto.subtle.importKey('raw', Uint8Array.from(atob(stored), (c) => c.charCodeAt(0)), 'AES-GCM', false, ['decrypt']);
  const cache = new Map();                                    // the same file is often used several times on a page

  async function decryptFile(url, options) {
    const response = await fetch(url, options);
    if (!response.ok) throw new Error(`${url}: ${response.status}`);
    const bytes = new Uint8Array(await response.arrayBuffer());     // 12-byte IV, then ciphertext + tag
    return crypto.subtle.decrypt({ name: 'AES-GCM', iv: bytes.slice(0, 12) }, await key, bytes.slice(12));
  }

  function load(url) {
    if (!cache.has(url)) {
      cache.set(url, (async () => {
        let plain;
        try {
          plain = await decryptFile(url);
        } catch (e) {
          // Not decryptable: most likely an older copy of the file left in the browser cache by a previous deployment
          // (or by a changed password). Ask the server again, bypassing the cache, before giving up.
          plain = await decryptFile(url, { cache: 'reload' });
        }
        const type = TYPES[url.replace(/\.enc$/, '').split('.').pop().toLowerCase()] || 'application/octet-stream';
        return URL.createObjectURL(new Blob([plain], { type }));
      })());
    }
    return cache.get(url);
  }

  // A few at a time, in reading order. A link around an image (the "open full size" links) gets the same blob URL.
  let next = 0;
  async function worker() {
    while (next < images.length) {
      const img = images[next++];
      try {
        const url = await load(img.dataset.encSrc);
        img.src = url;
        const link = img.closest('a[data-enc-href]');
        if (link) link.href = url;
      } catch (e) {
        img.dispatchEvent(new Event('error'));                // app.js removes it and the placeholder shows through
      }
    }
  }
  Promise.all(Array.from({ length: 4 }, worker));
})();
