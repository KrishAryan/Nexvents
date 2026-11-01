// Small progressive-enhancement helpers for NexVents.
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('form').forEach((form) => {
    // The QR image form is submitted with fetch and stays on the same page.
    // Do not disable its button; organisers may need to scan more than one ticket.
    if (form.id === 'qr-upload-form') return;

    form.addEventListener('submit', () => {
      const submit = form.querySelector('button[type="submit"]');
      if (submit && !submit.disabled) {
        submit.dataset.originalText = submit.innerHTML;
        submit.disabled = true;
        submit.style.opacity = '.72';
      }
    });
  });
});
