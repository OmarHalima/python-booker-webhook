// ==UserScript==
// @name         BMEIA Appointment — Turbo fetch injector (experimental)
// @namespace    bmeia-booker
// @version      0.1.0
// @description  Submit capture form via fetch from inside the browser tab (turbo option). Use only on appointment.bmeia.gv.at capture page.
// @match        https://appointment.bmeia.gv.at/*
// @grant        none
// @run-at       document-idle
// ==/UserScript==

/**
 * EXPERIMENTAL — "taktik al-talqa" prototype
 *
 * Prerequisites:
 * 1. You are on the Capture data page (#Token, #CaptchaText, #Captcha_CaptchaImage).
 * 2. Fill applicant fields manually OR call fillApplicant() before submitTurbo().
 * 3. Solve captcha text externally (OCR/Gemini) and pass to submitTurbo(captchaText).
 *
 * Playwright booker remains the stable path; this script is for speed experiments.
 */
(function () {
  'use strict';

  const ORIGIN = 'https://appointment.bmeia.gv.at';

  /** Fire keydown/keypress/input/keyup so BotDetect client scripts see human typing. */
  function simulateHumanCaptchaInput(el, text) {
    if (!el) return;
    el.focus();
    el.value = '';
    for (const ch of String(text || '')) {
      for (const type of ['keydown', 'keypress', 'input', 'keyup']) {
        el.dispatchEvent(
          new KeyboardEvent(type, {
            key: ch,
            bubbles: true,
            cancelable: true,
          })
        );
      }
      el.value += ch;
      el.dispatchEvent(new Event('input', { bubbles: true }));
    }
    el.blur();
  }

  /** Read fresh hidden fields + form action from live DOM (matches app.py extract_fresh_from_dom). */
  function readFreshFormState() {
    const form = document.querySelector('form');
    if (!form) throw new Error('No form on page');
    const val = (sel) => document.querySelector(sel)?.value || '';
    const action = form.getAttribute('action') || '/HomeWeb/AppointmentPassport';
    const url = action.startsWith('http') ? action : ORIGIN + action;
    return {
      url,
      token: val('#Token'),
      startTime: val('#StartTime'),
      bdcVcid: val('#BDC_VCID_Captcha'),
      bdcHs: val('#BDC_Hs_Captcha'),
      bdcSp: val('#BDC_SP_Captcha'),
      bdcBw: val('#BDC_BackWorkaround_Captcha') || '0',
    };
  }

  /** Build application/x-www-form-urlencoded body from current form + overrides. */
  function buildFormBody(captchaText, extra = {}) {
    const form = document.querySelector('form');
    if (!form) throw new Error('No form');
    const fd = new FormData(form);
    if (captchaText != null) fd.set('CaptchaText', String(captchaText).toUpperCase());
    fd.set('Command', 'Next');
    for (const [k, v] of Object.entries(extra)) {
      if (v != null) fd.set(k, String(v));
    }
    return new URLSearchParams(fd).toString();
  }

  /**
   * Turbo submit via fetch (experimental). Often CAPTCHA_FAIL — BotDetect rejects fetch POST
   * even with simulateHumanCaptchaInput. Prefer submitTurboForm() below.
   */
  async function submitTurbo(captchaText, extraFields = {}) {
    const captchaEl = document.querySelector('#CaptchaText');
    simulateHumanCaptchaInput(captchaEl, captchaText);
    const fresh = readFreshFormState();
    console.info('[bmeiaTurbo] StartTime from DOM:', fresh.startTime, '— verify before submit');
    const body = buildFormBody(captchaText, extraFields);
    const t0 = performance.now();
    const res = await fetch(fresh.url, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        Accept: 'text/html,application/xhtml+xml',
      },
      body,
    });
    const html = await res.text();
    const ms = Math.round(performance.now() - t0);
    const success = /Confirmation of reservation/i.test(html);
    const slotTaken = /allocated otherwise in the meantime/i.test(html);
    const alreadyBooked = /already reserved an appointment/i.test(html);
    const captchaFail = /text from the picture does not match/i.test(html);
    return {
      ok: res.ok,
      status: res.status,
      ms,
      resultType: success
        ? 'SUCCESS'
        : alreadyBooked
          ? 'ALREADY_BOOKED'
          : slotTaken
            ? 'SLOT_TAKEN'
            : captchaFail
              ? 'CAPTCHA_FAIL'
              : 'OTHER',
      htmlSnippet: html.slice(0, 500),
      fresh,
    };
  }

  /**
   * Recommended turbo path: human captcha events + real form submit (Next click).
   * Live test 2026-05-30: SUCCESS ~4s vs fetch CAPTCHA_FAIL with same OCR text.
   * After call, page navigates — read result from DOM title / confirmation heading.
   */
  function submitTurboForm(captchaText) {
    const captchaEl = document.querySelector('#CaptchaText');
    simulateHumanCaptchaInput(captchaEl, captchaText);
    const form = document.querySelector('form');
    const nextBtn = document.querySelector('#nextButton');
    if (!form) throw new Error('No form');
    let cmd = form.querySelector('input[type="hidden"][name="Command"]');
    if (!cmd) {
      cmd = document.createElement('input');
      cmd.type = 'hidden';
      cmd.name = 'Command';
      form.appendChild(cmd);
    }
    cmd.value = 'Next';
    console.info('[bmeiaTurbo] submitting via form.requestSubmit — StartTime:', readFreshFormState().startTime);
    form.requestSubmit(nextBtn || undefined);
    return readFreshFormState();
  }

  /** Classify current page after submitTurboForm navigation settles. */
  function classifyPageResult() {
    const html = document.documentElement?.outerHTML || '';
    if (/Confirmation of reservation/i.test(html)) return 'SUCCESS';
    if (/already reserved an appointment/i.test(html)) return 'ALREADY_BOOKED';
    if (/allocated otherwise in the meantime/i.test(html)) return 'SLOT_TAKEN';
    if (/text from the picture does not match/i.test(html)) return 'CAPTCHA_FAIL';
    return 'OTHER';
  }

  function fillApplicant(student) {
    const set = (id, v) => {
      const el = document.getElementById(id);
      if (el && v != null) {
        el.value = String(v);
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
      }
    };
    set('Lastname', student.Lastname);
    set('Firstname', student.Firstname);
    set('DateOfBirth', student.DateOfBirth);
    set('TraveldocumentNumber', student.TraveldocumentNumber);
    set('Sex', student.Sex);
    set('Street', student.Street);
    set('Postcode', student.Postcode);
    set('City', student.City);
    set('Country', student.Country);
    set('Telephone', student.Telephone);
    set('Email', student.Email);
  }

  // Expose on window for F12 console testing (Copy-as-fetch alternative):
  //   await bmeiaTurbo.fillApplicant({...}); await bmeiaTurbo.submitTurbo('ABC12')
  window.bmeiaTurbo = {
    simulateHumanCaptchaInput,
    readFreshFormState,
    buildFormBody,
    submitTurbo,
    submitTurboForm,
    classifyPageResult,
    fillApplicant,
  };

  console.info(
    '[bmeiaTurbo] loaded. Prefer submitTurboForm("CAPTCHA") — fetch submitTurbo() often CAPTCHA_FAIL.'
  );
})();
