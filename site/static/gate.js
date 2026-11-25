/* Password gate for a protected site.
   The real page is embedded in #payload, encrypted with AES-256-GCM under a key that is derived from the
   password (PBKDF2-HMAC-SHA256). Nothing readable is published: this script asks for the password, derives
   the key in the browser, decrypts the page and swaps it in. A wrong password makes the decryption fail.

   The derived key (not the password) is kept in sessionStorage for the lifetime of the tab, so moving
   between pages of the site does not ask again; closing the tab forgets it. */
(() => {
  'use strict';

  const KEY_STORE = 'gallery-key';
  const $ = (id) => document.getElementById(id);
  const fromB64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
  const toB64 = (bytes) => btoa(String.fromCharCode(...bytes));

  const form = $('gate-form');
  const input = $('gate-password');
  const error = $('gate-error');
  const status = $('gate-status');

  const payload = JSON.parse($('payload').textContent);
  const blob = fromB64(payload.data);                        // 12-byte IV, then ciphertext + tag

  const stored = {
    get() { try { return sessionStorage.getItem(KEY_STORE); } catch (e) { return null; } },
    set(v) { try { sessionStorage.setItem(KEY_STORE, v); } catch (e) { /* private mode: asks again on every page */ } },
    clear() { try { sessionStorage.removeItem(KEY_STORE); } catch (e) { /* ignore */ } },
  };

  /** Page HTML for this key; throws when the key is wrong (the GCM authentication tag does not match). */
  async function open(rawKey) {
    const key = await crypto.subtle.importKey('raw', rawKey, 'AES-GCM', false, ['decrypt']);
    const plain = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: blob.slice(0, 12) }, key, blob.slice(12));
    return new TextDecoder().decode(plain);
  }

  async function derive(password) {
    const base = await crypto.subtle.importKey('raw', new TextEncoder().encode(password), 'PBKDF2', false, ['deriveBits']);
    const bits = await crypto.subtle.deriveBits(
      { name: 'PBKDF2', hash: 'SHA-256', salt: fromB64(payload.salt), iterations: payload.iter }, base, 256);
    return new Uint8Array(bits);
  }

  function show(html) {
    document.open();
    document.write(html);
    document.close();
  }

  (async () => {
    if (!window.crypto || !crypto.subtle) {
      status.hidden = true;
      const p = document.createElement('p');
      p.className = 'gate-error';
      p.textContent = 'Your browser cannot decrypt this page. Please open the site over https:// in a current browser.';
      form.after(p);
      return;
    }

    const saved = stored.get();
    if (saved) {                                              // already unlocked in this tab
      try { return show(await open(fromB64(saved))); } catch (e) { stored.clear(); }
    }

    status.hidden = true;
    form.hidden = false;
    input.focus();

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      error.hidden = true;
      form.setAttribute('aria-busy', 'true');
      form.querySelector('button').disabled = true;
      try {
        const rawKey = await derive(input.value);
        const html = await open(rawKey);
        stored.set(toB64(rawKey));
        show(html);
      } catch (err) {
        error.textContent = 'That password is not correct. Please try again.';
        error.hidden = false;
        input.select();
      } finally {
        form.removeAttribute('aria-busy');
        form.querySelector('button').disabled = false;
      }
    });
  })();
})();
