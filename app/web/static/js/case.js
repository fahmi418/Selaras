/**
 * Selaras Field Officer Case Page — Client Interaction Logic
 * Handles checklist state persistence, copy-to-clipboard, outcome drawer,
 * currency formatting, and asynchronous case actions without emojis.
 */

(function () {
  'use strict';

  // SVG Icons dictionary for dynamic insertion (zero emoji)
  const ICONS = {
    check: `<svg viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"></polyline></svg>`,
    alert: `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>`,
    copy: `<svg viewBox="0 0 24 24"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>`,
  };

  // DOM Elements
  const caseContainer = document.querySelector('[data-case-token]');
  if (!caseContainer) return;

  const token = caseContainer.getAttribute('data-case-token');
  const caseId = caseContainer.getAttribute('data-case-id');
  const companyName = caseContainer.getAttribute('data-company-name') || 'Perusahaan';

  // --------------------------------------------------------------------------
  // 1. Checklist State Management (localStorage persistence)
  // --------------------------------------------------------------------------
  const checklistStorageKey = `selaras_cl_${caseId || token}`;
  const checklistInputs = document.querySelectorAll('.checklist-input');
  const checklistCounter = document.getElementById('checklist-counter');

  function loadChecklistState() {
    let saved = {};
    try {
      saved = JSON.parse(localStorage.getItem(checklistStorageKey) || '{}');
    } catch (e) {
      saved = {};
    }

    let checkedCount = 0;
    checklistInputs.forEach((input, index) => {
      const isChecked = Boolean(saved[index]);
      input.checked = isChecked;
      const itemEl = input.closest('.checklist-item');
      if (itemEl) {
        itemEl.classList.toggle('is-done', isChecked);
      }
      if (isChecked) checkedCount++;
    });

    updateChecklistCounter(checkedCount, checklistInputs.length);
  }

  function saveChecklistState() {
    const state = {};
    let checkedCount = 0;
    checklistInputs.forEach((input, index) => {
      state[index] = input.checked;
      const itemEl = input.closest('.checklist-item');
      if (itemEl) {
        itemEl.classList.toggle('is-done', input.checked);
      }
      if (input.checked) checkedCount++;
    });

    try {
      localStorage.setItem(checklistStorageKey, JSON.stringify(state));
    } catch (e) {
      // storage full or disabled
    }

    updateChecklistCounter(checkedCount, checklistInputs.length);
  }

  function updateChecklistCounter(checked, total) {
    if (checklistCounter) {
      checklistCounter.textContent = `${checked} dari ${total} diverifikasi`;
    }
  }

  checklistInputs.forEach((input) => {
    input.addEventListener('change', saveChecklistState);
  });

  loadChecklistState();

  // --------------------------------------------------------------------------
  // 2. Copy Checklist to Clipboard (formatted for field notes / WhatsApp)
  // --------------------------------------------------------------------------
  const copyBtn = document.getElementById('btn-copy-checklist');
  if (copyBtn) {
    copyBtn.addEventListener('click', function () {
      const items = [];
      items.push(`*CHECKLIST KUNJUNGAN SELARAS*`);
      items.push(`Entitas: ${companyName}`);
      items.push(`ID Kasus: ${caseId || '-'}`);
      items.push(`---------------------------------`);

      checklistInputs.forEach((input) => {
        const labelEl = input.closest('.checklist-item')?.querySelector('.checklist-label');
        const text = labelEl ? labelEl.textContent.trim() : '';
        const statusMark = input.checked ? '[V]' : '[ ]';
        items.push(`${statusMark} ${text}`);
      });

      items.push(`---------------------------------`);
      items.push(`Dicatat via Sistem Selaras BPJS Kesehatan`);

      const fullText = items.join('\n');

      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(fullText).then(() => {
          showToast('Checklist berhasil disalin ke clipboard', 'success');
        }).catch(() => {
          fallbackCopyText(fullText);
        });
      } else {
        fallbackCopyText(fullText);
      }
    });
  }

  function fallbackCopyText(text) {
    const textArea = document.createElement('textarea');
    textArea.value = text;
    textArea.style.position = 'fixed';
    textArea.style.left = '-9999px';
    document.body.appendChild(textArea);
    textArea.focus();
    textArea.select();
    try {
      document.execCommand('copy');
      showToast('Checklist disalin ke clipboard', 'success');
    } catch (err) {
      showToast('Gagal menyalin teks ke clipboard', 'danger');
    }
    document.body.removeChild(textArea);
  }

  // --------------------------------------------------------------------------
  // 3. Print / Export Functionality
  // --------------------------------------------------------------------------
  const printBtn = document.getElementById('btn-print-case');
  if (printBtn) {
    printBtn.addEventListener('click', () => window.print());
  }

  // --------------------------------------------------------------------------
  // 4. Drawer & Modal Management
  // --------------------------------------------------------------------------
  const outcomeDrawer = document.getElementById('outcome-drawer');
  const openOutcomeBtn = document.getElementById('btn-open-outcome');
  const closeOutcomeBtns = document.querySelectorAll('[data-close-drawer]');

  function openOutcomeModal() {
    if (outcomeDrawer) {
      outcomeDrawer.classList.add('active');
      document.body.style.overflow = 'hidden';
    }
  }

  function closeOutcomeModal() {
    if (outcomeDrawer) {
      outcomeDrawer.classList.remove('active');
      document.body.style.overflow = '';
    }
  }

  if (openOutcomeBtn) {
    openOutcomeBtn.addEventListener('click', openOutcomeModal);
  }

  closeOutcomeBtns.forEach((btn) => {
    btn.addEventListener('click', closeOutcomeModal);
  });

  if (outcomeDrawer) {
    outcomeDrawer.addEventListener('click', function (e) {
      if (e.target === outcomeDrawer) {
        closeOutcomeModal();
      }
    });
  }

  // --------------------------------------------------------------------------
  // 5. Currency Input Formatting (Rupiah)
  // --------------------------------------------------------------------------
  const amountInput = document.getElementById('found_amount_display');
  const rawAmountInput = document.getElementById('found_amount_raw');

  if (amountInput && rawAmountInput) {
    amountInput.addEventListener('input', function (e) {
      // Remove all non-digits
      const rawValue = this.value.replace(/\D/g, '');
      rawAmountInput.value = rawValue;

      if (!rawValue) {
        this.value = '';
        return;
      }

      // Format as IDR thousands (e.g. 15.000.000)
      const formatted = new Intl.NumberFormat('id-ID').format(Number(rawValue));
      this.value = formatted;
    });
  }

  // --------------------------------------------------------------------------
  // 6. Asynchronous Case Actions (Ambil / Tunda / Bukan Prioritas)
  // --------------------------------------------------------------------------
  window.handleCaseAction = async function (action) {
    let reason = '';

    if (action === 'tunda') {
      reason = prompt('Masukkan alasan penundaan kasus (contoh: Menunggu data slip tambahan):');
      if (reason === null) return; // User cancelled
    } else if (action === 'bukan_prioritas') {
      const confirmAction = confirm('Tandai kasus ini sebagai Bukan Prioritas? Tindakan ini akan menutup berkas kasus.');
      if (!confirmAction) return;
      reason = prompt('Masukkan catatan alasan non-prioritas (opsional):') || '';
    }

    try {
      const fd = new FormData();
      fd.append('action', action);
      if (reason) fd.append('reason', reason);

      const res = await fetch(`/case/${token}/action`, {
        method: 'POST',
        body: fd,
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Gagal memperbarui status kasus.');
      }

      const data = await res.json();
      showToast(`Status kasus berhasil diperbarui: ${data.new_status}`, 'success');

      if (action === 'ambil') {
        // Automatically reveal outcome form drawer for inspection input
        setTimeout(openOutcomeModal, 350);
      } else {
        setTimeout(() => window.location.reload(), 1200);
      }
    } catch (err) {
      showToast(err.message, 'danger');
    }
  };

  // --------------------------------------------------------------------------
  // 7. Outcome Submission Handler
  // --------------------------------------------------------------------------
  const outcomeForm = document.getElementById('form-outcome');
  if (outcomeForm) {
    outcomeForm.addEventListener('submit', async function (e) {
      e.preventDefault();
      const submitBtn = this.querySelector('button[type="submit"]');
      if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.textContent = 'Menyimpan...';
      }

      try {
        const fd = new FormData();
        const resultVal = document.getElementById('outcome_result')?.value;
        const modusVal = document.getElementById('outcome_modus')?.value;
        const rawAmount = rawAmountInput ? rawAmountInput.value : '0';
        const noteVal = document.getElementById('outcome_note')?.value || '';

        if (!resultVal) {
          throw new Error('Pilih hasil kunjungan terlebih dahulu.');
        }

        fd.append('result', resultVal);
        if (modusVal) fd.append('confirmed_modus', modusVal);
        if (rawAmount) fd.append('found_amount', rawAmount);
        if (noteVal) fd.append('note', noteVal);

        const res = await fetch(`/case/${token}/outcome`, {
          method: 'POST',
          body: fd,
        });

        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.detail || 'Gagal menyimpan hasil kunjungan.');
        }

        const data = await res.json();
        closeOutcomeModal();

        // Render success state
        renderOutcomeSuccess(data.outcome_id, resultVal, modusVal, rawAmount);
        showToast('Hasil kunjungan lapangan berhasil tersimpan.', 'success');
      } catch (err) {
        showToast(err.message, 'danger');
        if (submitBtn) {
          submitBtn.disabled = false;
          submitBtn.textContent = 'Simpan Hasil Kunjungan';
        }
      }
    });
  }

  function renderOutcomeSuccess(outcomeId, result, modus, amount) {
    const actionsDock = document.getElementById('case-action-dock');
    if (actionsDock) actionsDock.style.display = 'none';

    const statusContainer = document.getElementById('case-status-section');
    if (statusContainer) {
      const formattedAmount = amount && Number(amount) > 0
        ? `Rp ${new Intl.NumberFormat('id-ID').format(Number(amount))}`
        : '-';

      statusContainer.innerHTML = `
        <div class="card" style="border-color: var(--color-mint-pulse); background-color: rgba(46, 206, 160, 0.04);">
          <div class="card-title-row">
            <span class="card-title" style="color: var(--color-mint-pulse);">
              <span class="icon icon-sm">${ICONS.check}</span>
              Hasil Kunjungan Selesai Dicatat
            </span>
            <span class="badge badge-mint">STATUS: VISITED</span>
          </div>
          <p style="font-size: var(--text-body); color: var(--color-ink); margin-bottom: var(--space-8);">
            Kunjungan lapangan telah diverifikasi dan dicatat pada sistem audit BPJS Kesehatan.
          </p>
          <div style="font-size: var(--text-small); color: var(--color-graphite); line-height: 1.6;">
            <div><strong>Hasil:</strong> ${result}</div>
            ${modus ? `<div><strong>Modus Terkonfirmasi:</strong> Modus #${modus}</div>` : ''}
            <div><strong>Nilai Temuan:</strong> ${formattedAmount}</div>
            <div style="font-family: var(--font-mono); font-size: 11px; margin-top: var(--space-6); color: var(--color-slate);">
              ID Berkas: ${outcomeId}
            </div>
          </div>
        </div>
      `;
      statusContainer.scrollIntoView({ behavior: 'smooth' });
    }
  }

  // --------------------------------------------------------------------------
  // 8. Toast Helper
  // --------------------------------------------------------------------------
  function showToast(message, type = 'info') {
    let container = document.getElementById('toast-container');
    if (!container) {
      container = document.createElement('div');
      container.id = 'toast-container';
      container.className = 'toast-container';
      document.body.appendChild(container);
    }

    const toast = document.createElement('div');
    toast.className = 'toast';

    let iconSvg = ICONS.check;
    if (type === 'danger') {
      toast.style.backgroundColor = 'var(--color-danger)';
      iconSvg = ICONS.alert;
    }

    toast.innerHTML = `
      <span class="icon icon-sm">${iconSvg}</span>
      <span>${message}</span>
    `;

    container.appendChild(toast);

    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateY(-10px)';
      toast.style.transition = 'all 200ms ease';
      setTimeout(() => toast.remove(), 250);
    }, 3200);
  }

  window.showToast = showToast;
})();
